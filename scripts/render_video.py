#!/usr/bin/env python3
"""
scripts/render_video.py

STEP 2 of the YouTube automation pipeline — turns the JSON script from
api/generate-video-script.js into an actual .mp4 video, 100% free tools:

  1. Call the deployed generate-video-script API to get a fresh script + metadata
  2. Generate voiceover audio per scene using Edge-TTS (free, no API key)
  3. Fill each scene's duration with a CHAIN of fresh visuals (video clips and/or
     still images) — nothing ever loops/repeats. If one clip/image runs out
     before the voiceover for that scene finishes, we fetch a DIFFERENT visual
     for the remaining time instead of looping the same one. Images are capped
     at ~4 seconds each (never longer, never repeated) before switching to the
     next visual.
  4. Burn in on-screen captions with ffmpeg
  5. Concatenate all scenes into one final video
  6. Generate a simple thumbnail image (text over the first frame)
  7. Save video + thumbnail + metadata.json into ./output/ for GitHub Actions
     to upload as workflow artifacts

This does NOT upload to YouTube — that's handled by upload_youtube.py.

Required environment variables (set as GitHub Actions secrets):
  VERCEL_APP_URL   - e.g. https://asfitech.vercel.app  (no trailing slash)
  CRON_SECRET      - same secret used by the Vercel API
  PEXELS_API_KEY   - free from pexels.com/api
"""

import json
import os
import random
import subprocess
import sys
import urllib.request
import urllib.parse
import urllib.error

OUTPUT_DIR = "output"
SCENES_DIR = os.path.join(OUTPUT_DIR, "scenes")
VOICE = "en-US-GuyNeural"  # free Edge-TTS voice, clear + neutral for explainers

IMAGE_MAX_SECONDS = 4.0      # a still image is shown for at most this long, then switches
VIDEO_CHUNK_MAX_SECONDS = 8.0  # a single video clip segment is trimmed to at most this long

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def log(msg):
    print(f"[render_video] {msg}", flush=True)


def fetch_script():
    """STEP 1: call the deployed Vercel endpoint to get a fresh trending script."""
    base_url = os.environ["VERCEL_APP_URL"].rstrip("/")
    secret = os.environ["CRON_SECRET"]
    url = f"{base_url}/api/generate-video-script?secret={urllib.parse.quote(secret)}"
    log(f"Fetching script from {base_url}/api/generate-video-script ...")
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("success"):
        raise RuntimeError(f"generate-video-script API did not return success: {data}")
    return data


def tts_scene(text, out_path):
    """Edge-TTS voiceover for one scene. Free, no API key needed."""
    cmd = ["edge-tts", "--voice", VOICE, "--text", text, "--write-media", out_path]
    subprocess.run(cmd, check=True)


def get_media_duration(path):
    """Works for both audio and video files."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def download_file(url, out_path):
    """Download with a normal browser User-Agent (Cloudflare, which sits in
    front of Pexels, blocks the plain default urllib agent as a bot)."""
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(out_path, "wb") as f:
        f.write(resp.read())


def fetch_pexels_clip(query, out_path, exclude_urls=None):
    """Grab ONE free stock video clip matching the query. Picks randomly among
    the top results (and skips anything in exclude_urls) so repeated calls for
    the same scene/query don't always return the identical clip."""
    exclude_urls = exclude_urls or set()
    api_key = os.environ["PEXELS_API_KEY"]
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": query, "per_page": 8, "orientation": "landscape"}
    )
    req = urllib.request.Request(url, headers={"Authorization": api_key, "User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log(f"  Pexels API error {e.code} for query '{query}': {e.read().decode(errors='replace')[:200]}")
        return None
    except Exception as e:
        log(f"  Pexels request failed for query '{query}': {e}")
        return None

    videos = [v for v in data.get("videos", []) if v.get("url") not in exclude_urls]
    if not videos:
        return None
    random.shuffle(videos)

    for video in videos:
        files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0))
        for f in files:
            if 1280 <= f.get("width", 0) <= 1920:
                try:
                    download_file(f["link"], out_path)
                    return {"path": out_path, "id": video.get("url")}
                except Exception as e:
                    log(f"  download failed: {e}")
                    continue
    return None


def fetch_pexels_photo(query, out_path, exclude_urls=None):
    """Grab ONE free Pexels still photo matching the query, avoiding repeats."""
    exclude_urls = exclude_urls or set()
    api_key = os.environ["PEXELS_API_KEY"]
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": query, "per_page": 8, "orientation": "landscape"}
    )
    req = urllib.request.Request(url, headers={"Authorization": api_key, "User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"  Pexels photo search failed for '{query}': {e}")
        return None

    photos = [p for p in data.get("photos", []) if p.get("url") not in exclude_urls]
    if not photos:
        return None
    random.shuffle(photos)

    photo = photos[0]
    try:
        src = photo["src"].get("large2x") or photo["src"].get("original")
        download_file(src, out_path)
        return {"path": out_path, "id": photo.get("url")}
    except Exception as e:
        log(f"  Failed to download Pexels photo for '{query}': {e}")
        return None


def make_video_segment(clip_path, audio_path, audio_offset, seg_duration, drawtext, out_path):
    """One segment: a video clip trimmed to seg_duration (played once, never
    looped), paired with the correct slice of the REAL voiceover starting at
    audio_offset — this is what keeps the voice continuous across segments."""
    vf = f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080{drawtext}"
    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-ss", str(audio_offset), "-i", audio_path,
        "-vf", vf,
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(seg_duration),
        "-c:v", "libx264", "-c:a", "aac", "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def make_photo_segment(photo_path, audio_path, audio_offset, seg_duration, drawtext, out_path):
    """One segment: a still image with a slow Ken Burns zoom, shown for
    exactly seg_duration (never looped/repeated), paired with the correct
    slice of the real voiceover."""
    fps = 30
    frames = max(1, int(seg_duration * fps))
    vf = (
        f"scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,"
        f"zoompan=z='min(zoom+0.0007,1.2)':d={frames}:s=1920x1080:fps={fps}{drawtext}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", photo_path,
        "-ss", str(audio_offset), "-i", audio_path,
        "-vf", vf,
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(seg_duration),
        "-c:v", "libx264", "-c:a", "aac",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def make_background_segment(audio_path, audio_offset, seg_duration, drawtext, out_path):
    """Last-resort segment when no clip/photo is available: plain on-brand
    background color, still paired with the correct real voiceover slice."""
    vf = f"color=c=0x0b1a33:s=1920x1080{drawtext}"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"{vf}:d={seg_duration}",
        "-ss", str(audio_offset), "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(seg_duration),
        "-c:v", "libx264", "-c:a", "aac",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def build_scene_clip(index, scene, scenes_dir):
    """Build one finished scene by CHAINING fresh visual segments (never
    looping the same clip/image) until the full voiceover duration is
    covered, then joining them into one scene file."""
    audio_path = os.path.join(scenes_dir, f"scene_{index}.mp3")
    tts_scene(scene["voiceover"], audio_path)
    total_duration = get_media_duration(audio_path)
    log(f"  scene {index}: voiceover {total_duration:.1f}s — '{scene['voiceover'][:50]}...'")

    caption = scene.get("on_screen_text", "").replace(":", "\\:").replace("'", "\u2019")
    drawtext = ""
    if caption:
        drawtext = (
            f",drawtext=text='{caption}':fontcolor=white:fontsize=54:"
            f"box=1:boxcolor=black@0.55:boxborderw=20:"
            f"x=(w-text_w)/2:y=h-180:font='DejaVu Sans Bold'"
        )

    segments = []
    used_video_ids = set()
    used_photo_ids = set()
    offset = 0.0
    segment_num = 0
    prefer_video = True  # alternate starting preference per scene call for variety

    while offset < total_duration - 0.05 and segment_num < 6:  # safety cap on chunk count
        remaining = total_duration - offset
        seg_path = os.path.join(scenes_dir, f"scene_{index}_seg{segment_num}.mp4")

        got_segment = False

        if prefer_video:
            raw_path = os.path.join(scenes_dir, f"scene_{index}_seg{segment_num}_raw.mp4")
            result = fetch_pexels_clip(scene["visual_note"], raw_path, exclude_urls=used_video_ids)
            if result:
                used_video_ids.add(result["id"])
                clip_actual_duration = get_media_duration(result["path"])
                seg_duration = min(clip_actual_duration, remaining, VIDEO_CHUNK_MAX_SECONDS)
                make_video_segment(result["path"], audio_path, offset, seg_duration, drawtext, seg_path)
                got_segment = True

        if not got_segment:
            raw_path = os.path.join(scenes_dir, f"scene_{index}_seg{segment_num}_photo.jpg")
            result = fetch_pexels_photo(scene["visual_note"], raw_path, exclude_urls=used_photo_ids)
            if result:
                used_photo_ids.add(result["id"])
                seg_duration = min(remaining, IMAGE_MAX_SECONDS)
                make_photo_segment(result["path"], audio_path, offset, seg_duration, drawtext, seg_path)
                got_segment = True

        if not got_segment and prefer_video:
            # video attempt failed this round — try photo before giving up entirely
            raw_path = os.path.join(scenes_dir, f"scene_{index}_seg{segment_num}_photo2.jpg")
            result = fetch_pexels_photo(scene["visual_note"], raw_path, exclude_urls=used_photo_ids)
            if result:
                used_photo_ids.add(result["id"])
                seg_duration = min(remaining, IMAGE_MAX_SECONDS)
                make_photo_segment(result["path"], audio_path, offset, seg_duration, drawtext, seg_path)
                got_segment = True

        if not got_segment:
            # Nothing found at all for this chunk — fill the REST with plain
            # background rather than looping anything or leaving silence.
            make_background_segment(audio_path, offset, remaining, drawtext, seg_path)
            segments.append(seg_path)
            offset = total_duration
            break

        segments.append(seg_path)
        offset += seg_duration
        segment_num += 1
        prefer_video = not prefer_video  # alternate next chunk for visual variety

    # Safety: if the loop cap was hit before covering the full duration,
    # top off with one final background segment for whatever remains.
    if offset < total_duration - 0.05:
        seg_path = os.path.join(scenes_dir, f"scene_{index}_seg_final.mp4")
        make_background_segment(audio_path, offset, total_duration - offset, drawtext, seg_path)
        segments.append(seg_path)

    if len(segments) == 1:
        return segments[0]

    out_path = os.path.join(scenes_dir, f"scene_{index}_final.mp4")
    concatenate_videos(segments, out_path)
    return out_path


def concatenate_videos(video_paths, output):
    """Join any number of video files without re-encoding."""
    list_path = output.replace(".mp4", "_list.txt")
    with open(list_path, "w") as f:
        for p in video_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-c:a", "aac", output,
    ]
    subprocess.run(cmd, check=True)
    return output


def build_thumbnail(video_path, text, out_path):
    """Grab a frame + overlay punchy thumbnail text."""
    frame_path = os.path.join(OUTPUT_DIR, "_frame.png")
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01", "-vframes", "1", frame_path],
        check=True,
    )
    wrapped = text.replace(":", "\\:").replace("'", "\u2019")
    drawtext = (
        f"drawtext=text='{wrapped}':fontcolor=white:fontsize=90:"
        f"box=1:boxcolor=black@0.6:boxborderw=30:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:font='DejaVu Sans Bold'"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", frame_path, "-vf", drawtext, out_path],
        check=True,
    )
    os.remove(frame_path)


def main():
    os.makedirs(SCENES_DIR, exist_ok=True)

    data = fetch_script()
    log(f"Topic: {data['video_title']}")

    scenes = data["script"]
    scene_paths = []
    for i, scene in enumerate(scenes, start=1):
        scene_paths.append(build_scene_clip(i, scene, SCENES_DIR))

    final_video = os.path.join(OUTPUT_DIR, "final_video.mp4")
    log("Concatenating all scenes into final video...")
    concatenate_videos(scene_paths, final_video)

    thumb_text = data.get("thumbnail_text_ideas", [data["video_title"]])[0]
    thumb_path = os.path.join(OUTPUT_DIR, "thumbnail.png")
    log(f"Building thumbnail: '{thumb_text}'")
    build_thumbnail(final_video, thumb_text, thumb_path)

    metadata = {
        "title": data["video_title"],
        "description": data["description"],
        "tags": data["tags"],
        "chapters": data["chapters"],
        "aeo_qa_block": data["aeo_qa_block"],
        "source_topic": data["source_topic"],
    }
    with open(os.path.join(OUTPUT_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    log("Done. Output is in ./output/ (final_video.mp4, thumbnail.png, metadata.json)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FAILED: {e}")
        sys.exit(1)

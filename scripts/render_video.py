#!/usr/bin/env python3
"""
scripts/render_video.py

STEP 2 of the YouTube automation pipeline — turns the JSON script from
api/generate-video-script.js into an actual .mp4 video, 100% free tools:

  1. Call the deployed generate-video-script API to get a fresh script + metadata
  2. Generate voiceover audio per scene using Edge-TTS (free, no API key)
  3. Fetch matching free stock footage per scene from Pexels (using visual_note)
  4. Burn in on-screen captions with ffmpeg
  5. Concatenate all scenes into one final video
  6. Generate a simple thumbnail image (text over the first frame)
  7. Save video + thumbnail + metadata.json into ./output/ for GitHub Actions
     to upload as workflow artifacts

This does NOT upload to YouTube — that's Step 3, and stays a manual/reviewed
step for now, on purpose (see README: quality/policy reasons to keep a human
review point before anything goes public).

Required environment variables (set as GitHub Actions secrets):
  VERCEL_APP_URL   - e.g. https://asfitech.vercel.app  (no trailing slash)
  CRON_SECRET      - same secret used by the Vercel API
  PEXELS_API_KEY   - free from pexels.com/api
"""

import json
import os
import subprocess
import sys
import textwrap
import urllib.request
import urllib.parse
import urllib.error

OUTPUT_DIR = "output"
SCENES_DIR = os.path.join(OUTPUT_DIR, "scenes")
VOICE = "en-US-GuyNeural"  # free Edge-TTS voice, clear + neutral for explainers


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
    """STEP 2: Edge-TTS voiceover for one scene. Free, no API key needed."""
    cmd = ["edge-tts", "--voice", VOICE, "--text", text, "--write-media", out_path]
    subprocess.run(cmd, check=True)


def get_audio_duration(path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def fetch_pexels_clip(query, out_path, min_duration=3):
    """STEP 3: grab one free stock video clip matching the scene's visual_note.
    Returns None (not a crash) if Pexels errors out — the caller falls back
    to a plain background so one bad API call doesn't kill the whole render."""
    api_key = os.environ["PEXELS_API_KEY"]
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": query, "per_page": 5, "orientation": "landscape"}
    )
    req = urllib.request.Request(url, headers={"Authorization": api_key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log(f"  Pexels API error {e.code} for query '{query}': {body[:300]}")
        return None
    except Exception as e:
        log(f"  Pexels request failed for query '{query}': {e}")
        return None

    videos = data.get("videos", [])
    if not videos:
        log(f"  no Pexels results for '{query}', will use a plain color background instead")
        return None

    # Prefer an HD-ish file, not the largest (keeps download + render fast)
    try:
        for video in videos:
            files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0))
            for f in files:
                if f.get("width", 0) >= 1280 and f.get("width", 0) <= 1920:
                    urllib.request.urlretrieve(f["link"], out_path)
                    return out_path
        # fallback: just take the first file of the first result
        first = videos[0]["video_files"][0]["link"]
        urllib.request.urlretrieve(first, out_path)
        return out_path
    except Exception as e:
        log(f"  Failed to download Pexels clip for '{query}': {e}")
        return None


def build_scene_clip(index, scene, scenes_dir):
    """Build one finished scene: voiceover + matching clip + burned-in caption,
    trimmed/looped to match the voiceover length exactly."""
    audio_path = os.path.join(scenes_dir, f"scene_{index}.mp3")
    tts_scene(scene["voiceover"], audio_path)
    duration = get_audio_duration(audio_path)
    log(f"  scene {index}: voiceover {duration:.1f}s — '{scene['voiceover'][:50]}...'")

    raw_clip = os.path.join(scenes_dir, f"scene_{index}_raw.mp4")
    clip = fetch_pexels_clip(scene["visual_note"], raw_clip)

    out_path = os.path.join(scenes_dir, f"scene_{index}_final.mp4")
    caption = scene.get("on_screen_text", "").replace(":", "\\:").replace("'", "\u2019")

    drawtext = ""
    if caption:
        drawtext = (
            f",drawtext=text='{caption}':fontcolor=white:fontsize=54:"
            f"box=1:boxcolor=black@0.55:boxborderw=20:"
            f"x=(w-text_w)/2:y=h-180:font='DejaVu Sans Bold'"
        )

    if clip:
        # Loop/trim the stock clip to exactly match voiceover duration, scale to 1920x1080,
        # crop to fill (no black bars), then burn in the caption.
        vf = f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080{drawtext}"
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", clip,
            "-i", audio_path,
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", str(duration),
            "-c:v", "libx264", "-c:a", "aac", "-shortest",
            out_path,
        ]
    else:
        # No stock footage found — fall back to a plain dark-blue background
        # (still on-brand with the channel's blue/purple theme) instead of failing.
        vf = f"color=c=0x0b1a33:s=1920x1080{drawtext}"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"{vf}:d={duration}",
            "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-c:a", "aac", "-shortest",
            out_path,
        ]
    subprocess.run(cmd, check=True)
    return out_path


def concatenate_scenes(scene_paths, final_path):
    """STEP 5: join all scene clips into one final video."""
    list_path = os.path.join(SCENES_DIR, "concat_list.txt")
    with open(list_path, "w") as f:
        for p in scene_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-c:a", "aac", final_path,
    ]
    subprocess.run(cmd, check=True)


def build_thumbnail(video_path, text, out_path):
    """STEP 6: grab a frame + overlay punchy thumbnail text."""
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
    concatenate_scenes(scene_paths, final_video)

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

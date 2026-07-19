#!/usr/bin/env python3
"""
scripts/upload_youtube.py

STEP 3 of the pipeline — takes the output of render_video.py
(output/final_video.mp4, output/thumbnail.png, output/metadata.json) and
uploads it directly to YouTube using the YouTube Data API v3.

Required environment variables (GitHub Actions secrets):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
    - from the one-time scripts/get_refresh_token.py run (see that file's
      docstring for setup steps)

Optional environment variable:
  YT_PRIVACY_STATUS - "public" (default), "unlisted", or "private"
    Note: only "public" videos count toward the 4,000 watch-hour
    requirement for YouTube Partner Program monetization eligibility —
    "unlisted" is useful for a first test run without going live yet.
"""

import json
import os
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

OUTPUT_DIR = "output"
CATEGORY_ID = "28"  # Science & Technology, matches the topic-selection niche


def log(msg):
    print(f"[upload_youtube] {msg}", flush=True)


def get_youtube_client():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return build("youtube", "v3", credentials=creds)


def build_description(metadata):
    """Append the AEO Q&A block into the description as plain text, since
    YouTube descriptions are plain text (no markdown/HTML rendering)."""
    desc = metadata["description"]
    qa = metadata.get("aeo_qa_block", [])
    if qa:
        desc += "\n\n---\nFAQ:\n"
        for item in qa:
            desc += f"\nQ: {item['q']}\nA: {item['a']}\n"
    return desc


def main():
    with open(os.path.join(OUTPUT_DIR, "metadata.json")) as f:
        metadata = json.load(f)

    video_path = os.path.join(OUTPUT_DIR, "final_video.mp4")
    thumb_path = os.path.join(OUTPUT_DIR, "thumbnail.png")
    privacy_status = os.environ.get("YT_PRIVACY_STATUS", "public")

    log(f"Uploading: {metadata['title']}")
    log(f"Privacy status: {privacy_status}")

    youtube = get_youtube_client()

    body = {
        "snippet": {
            "title": metadata["title"][:100],
            "description": build_description(metadata)[:5000],
            "tags": metadata.get("tags", [])[:15],
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"  upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    log(f"Uploaded. Video ID: {video_id}")
    log(f"URL: https://www.youtube.com/watch?v={video_id}")

    # Set the custom thumbnail
    if os.path.exists(thumb_path):
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_path)).execute()
        log("Thumbnail set.")

    # Write the video_id + url out so the workflow can surface it in the
    # Actions run summary / artifact for easy reference.
    with open(os.path.join(OUTPUT_DIR, "upload_result.json"), "w") as f:
        json.dump(
            {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"},
            f,
            indent=2,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FAILED: {e}")
        sys.exit(1)

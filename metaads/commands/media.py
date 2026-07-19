"""Commands: image-upload, video-upload.

Media uploads execute directly (no --confirm): they only fill the account's
media library, cannot go live and spend nothing. Documented exception to the
dry-run-by-default rule.
"""

from __future__ import annotations

import base64
import json
import os
import time

from metaads import api, lint
from metaads.commands.common import account_of
from metaads.formatting import _die, _err, _output_json


def cmd_image_upload(args) -> None:
    """Upload image file, returns image hash."""
    account_id = account_of(args)

    lint.lint_image(args.file)

    with open(args.file, "rb") as f:
        file_bytes = base64.b64encode(f.read()).decode("ascii")

    filename = os.path.basename(args.file)
    data = api._api_call("POST", f"{account_id}/adimages", {
        "bytes": file_bytes,
        "name": filename,
    })

    # Response format: {"images": {"filename": {"hash": "...", "url": "..."}}}
    images = data.get("images", {})
    img_data = next(iter(images.values()), {}) if images else {}

    if args.json:
        _output_json(img_data)
    else:
        print(f"Image uploaded: {filename}")
        print(f"  Hash:  {img_data.get('hash', '---')}")
        print(f"  URL:   {img_data.get('url', '---')}")


def _wait_video_ready(video_id: str, timeout: int = 300, interval: int = 6) -> str:
    """Poll a video until processing completes. Returns final status string.

    Meta returns a video ID immediately after upload, but the video is still
    'processing'. Creating a creative that references a not-yet-ready video can
    fail, so callers that immediately build a creative should wait for 'ready'.
    """
    waited = 0
    status = "unknown"
    while waited < timeout:
        data = api._api_call("GET", video_id, {"fields": "status"})
        status = (data.get("status") or {}).get("video_status", "unknown")
        _err(f"  video {video_id} status={status} ({waited}s)")
        if status == "ready":
            return status
        if status == "error":
            _err(f"  VIDEO PROCESSING ERROR: {json.dumps(data.get('status'))}")
            return status
        time.sleep(interval)
        waited += interval
    return status


def cmd_video_upload(args) -> None:
    """Upload video file, returns video ID."""
    account_id = account_of(args)

    if not os.path.isfile(args.file):
        _die(f"ERROR: File not found: {args.file}")

    file_size = os.path.getsize(args.file)
    _err(f"Uploading {os.path.basename(args.file)} ({file_size / 1024 / 1024:.1f} MB)...")

    params: dict = {}
    if args.title:
        params["title"] = args.title

    with open(args.file, "rb") as f:
        data = api._api_call(
            "POST",
            f"{account_id}/advideos",
            params,
            files={"source": (os.path.basename(args.file), f)},
            timeout=300,
        )

    video_id = data.get("id", "")
    final_status = None
    if getattr(args, "wait", False) and video_id:
        final_status = _wait_video_ready(video_id, timeout=args.wait_timeout)
        if isinstance(data, dict):
            data["video_status"] = final_status

    if args.json:
        _output_json(data)
    else:
        print(f"Video uploaded: ID {video_id or '---'}")
        if final_status is not None:
            print(f"  Processing status: {final_status}")

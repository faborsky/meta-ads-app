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


# Single multipart POST returns HTTP 413 somewhere above ~200 MB (observed
# live at 234 MB) — switch to the resumable chunked upload well below that.
CHUNKED_THRESHOLD_MB = 100


def _upload_video_multipart(account_id: str, file_path: str, params: dict) -> dict:
    """Small files: one multipart POST."""
    with open(file_path, "rb") as f:
        return api._api_call(
            "POST",
            f"{account_id}/advideos",
            params,
            files={"source": (os.path.basename(file_path), f)},
            timeout=300,
        )


def _upload_video_chunked(account_id: str, file_path: str, params: dict) -> dict:
    """Large files: resumable upload (upload_phase start/transfer/finish).

    The server dictates chunk boundaries via returned start/end offsets.
    Transfer chunks are idempotent (offset-addressed), so transient errors
    retry safely — unlike every other write in this CLI.
    """
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    session = api._api_call("POST", f"{account_id}/advideos", {
        "upload_phase": "start", "file_size": file_size,
    })
    session_id = session.get("upload_session_id")
    video_id = session.get("video_id", "")
    if not session_id:
        _die(f"ERROR: Chunked upload start returned no upload_session_id: {json.dumps(session)}")

    start, end = int(session.get("start_offset", 0)), int(session.get("end_offset", 0))
    with open(file_path, "rb") as f:
        while start < end:
            f.seek(start)
            chunk = f.read(end - start)
            _err(f"  chunk {start / 1024 / 1024:.0f}–{end / 1024 / 1024:.0f} MB "
                 f"of {file_size / 1024 / 1024:.0f} MB")
            resp = api._api_call(
                "POST",
                f"{account_id}/advideos",
                {"upload_phase": "transfer", "upload_session_id": session_id,
                 "start_offset": start},
                files={"video_file_chunk": (filename, chunk)},
                timeout=300,
                retry_transient_writes=True,
            )
            start, end = int(resp.get("start_offset", end)), int(resp.get("end_offset", end))

    finish = api._api_call("POST", f"{account_id}/advideos", {
        "upload_phase": "finish", "upload_session_id": session_id, **params,
    })
    return {"id": video_id, "success": finish.get("success")}


def cmd_video_upload(args) -> None:
    """Upload video file, returns video ID."""
    account_id = account_of(args)

    if not os.path.isfile(args.file):
        _die(f"ERROR: File not found: {args.file}")

    file_size = os.path.getsize(args.file)
    use_chunked = args.chunked or file_size > CHUNKED_THRESHOLD_MB * 1024 * 1024
    _err(f"Uploading {os.path.basename(args.file)} ({file_size / 1024 / 1024:.1f} MB, "
         f"{'chunked' if use_chunked else 'single request'})...")

    params: dict = {}
    if args.title:
        params["title"] = args.title

    if use_chunked:
        data = _upload_video_chunked(account_id, args.file, params)
    else:
        data = _upload_video_multipart(account_id, args.file, params)

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

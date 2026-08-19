"""video-upload: single-request vs resumable chunked path (issue #3)."""

import json
from argparse import Namespace

from metaads import api
from metaads.commands import media


def _upload_args(path, **over):
    base = dict(file=str(path), title=None, chunked=False, wait=False,
                wait_timeout=300, confirm=False, json=True, account_id=None)
    base.update(over)
    return Namespace(**base)


def _video_file(tmp_path, size=1024):
    p = tmp_path / "spot.mp4"
    p.write_bytes(b"x" * size)
    return p


def test_small_file_uses_single_multipart_post(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_call(method, endpoint, params=None, files=None, **k):
        calls.append({"params": dict(params or {}), "files": files})
        return {"id": "vid1"}

    monkeypatch.setattr(api, "_api_call", fake_call)
    media.cmd_video_upload(_upload_args(_video_file(tmp_path)))
    assert len(calls) == 1
    assert "source" in calls[0]["files"]
    assert "upload_phase" not in calls[0]["params"]
    assert json.loads(capsys.readouterr().out)["id"] == "vid1"


def test_chunked_flag_forces_resumable_flow(monkeypatch, tmp_path, capsys):
    phases = []

    def fake_call(method, endpoint, params=None, files=None, **k):
        phase = (params or {}).get("upload_phase")
        phases.append(phase)
        if phase == "start":
            return {"upload_session_id": "s1", "video_id": "vid9",
                    "start_offset": "0", "end_offset": "1024"}
        if phase == "transfer":
            return {"start_offset": "1024", "end_offset": "1024"}
        return {"success": True}

    monkeypatch.setattr(api, "_api_call", fake_call)
    media.cmd_video_upload(_upload_args(_video_file(tmp_path), chunked=True))
    assert phases == ["start", "transfer", "finish"]
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "vid9"
    assert out["success"] is True


def test_large_file_switches_to_chunked_automatically(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(media, "CHUNKED_THRESHOLD_MB", 0)  # any file is "large"
    phases = []

    def fake_call(method, endpoint, params=None, files=None, **k):
        phase = (params or {}).get("upload_phase")
        phases.append(phase)
        if phase == "start":
            return {"upload_session_id": "s1", "video_id": "vid2",
                    "start_offset": "0", "end_offset": "512"}
        if phase == "transfer":
            # server drives chunk boundaries: two chunks of 512
            start = int(params["start_offset"]) + 512
            return {"start_offset": str(start), "end_offset": "1024"}
        return {"success": True}

    monkeypatch.setattr(api, "_api_call", fake_call)
    media.cmd_video_upload(_upload_args(_video_file(tmp_path, size=1024)))
    assert phases == ["start", "transfer", "transfer", "finish"]


def test_chunked_transfer_sends_offset_addressed_chunks(monkeypatch, tmp_path):
    p = tmp_path / "spot.mp4"
    p.write_bytes(b"A" * 512 + b"B" * 512)
    sent = []

    def fake_call(method, endpoint, params=None, files=None, **k):
        phase = (params or {}).get("upload_phase")
        if phase == "start":
            return {"upload_session_id": "s1", "video_id": "v",
                    "start_offset": "0", "end_offset": "512"}
        if phase == "transfer":
            sent.append((params["start_offset"], files["video_file_chunk"][1]))
            assert k.get("retry_transient_writes") is True
            start = int(params["start_offset"]) + 512
            return {"start_offset": str(start), "end_offset": "1024"}
        return {"success": True}

    monkeypatch.setattr(api, "_api_call", fake_call)
    media.cmd_video_upload(_upload_args(p, chunked=True))
    assert sent == [(0, b"A" * 512), (512, b"B" * 512)]


def test_chunked_title_travels_in_finish_phase(monkeypatch, tmp_path):
    finish_params = {}

    def fake_call(method, endpoint, params=None, files=None, **k):
        phase = (params or {}).get("upload_phase")
        if phase == "start":
            return {"upload_session_id": "s1", "video_id": "v",
                    "start_offset": "0", "end_offset": "4"}
        if phase == "transfer":
            return {"start_offset": "4", "end_offset": "4"}
        finish_params.update(params)
        return {"success": True}

    monkeypatch.setattr(api, "_api_call", fake_call)
    p = tmp_path / "t.mp4"
    p.write_bytes(b"abcd")
    media.cmd_video_upload(_upload_args(p, chunked=True, title="Letní spot"))
    assert finish_params["title"] == "Letní spot"

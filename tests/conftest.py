"""Shared fixtures: no test may ever touch the real API, .env or .usage/."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metaads import api  # noqa: E402


class DummyResp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload=None, headers=None, status_code=200, text=""):
        self._payload = {} if payload is None else payload
        self.headers = headers or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def dummy_resp():
    return DummyResp


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Redirect all persistent state to a temp dir and fake the credentials."""
    monkeypatch.setattr(api, "USAGE_DIR", str(tmp_path / ".usage"))
    monkeypatch.setattr(api, "META_ACCESS_TOKEN", "FAKETESTTOKEN123")
    monkeypatch.setattr(api, "META_AD_ACCOUNT_ID", "act_1000000")
    monkeypatch.delenv("METAADS_IGNORE_USAGE_GUARD", raising=False)
    # No test sleeps for real.
    monkeypatch.setattr(api.time, "sleep", lambda s: None)

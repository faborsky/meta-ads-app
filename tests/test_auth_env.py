"""token-extend .env handling: atomic rewrite, permissions, backup."""

import os
import stat

from metaads import api
from metaads.commands.auth import _write_env_token


def test_write_env_token_preserves_other_vars_and_backs_up(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "META_ACCESS_TOKEN=oldtoken\n"
        "META_AD_ACCOUNT_ID=act_123\n"
        "META_PAGE_ID=456\n"
    )
    monkeypatch.setattr(api, "BASE_DIR", str(tmp_path))

    _write_env_token("newtoken")

    content = env.read_text()
    assert "META_ACCESS_TOKEN=newtoken" in content
    assert "META_AD_ACCOUNT_ID=act_123" in content
    assert "META_PAGE_ID=456" in content
    assert "oldtoken" not in content

    bak = tmp_path / ".env.bak"
    assert "META_ACCESS_TOKEN=oldtoken" in bak.read_text()


def test_write_env_token_sets_0600_and_leaves_no_temp_files(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("META_ACCESS_TOKEN=oldtoken\n")
    monkeypatch.setattr(api, "BASE_DIR", str(tmp_path))

    _write_env_token("newtoken")

    for name in (".env", ".env.bak"):
        mode = stat.S_IMODE(os.stat(tmp_path / name).st_mode)
        assert mode == 0o600, f"{name} has mode {oct(mode)}"
    leftovers = [p for p in os.listdir(tmp_path) if ".tmp." in p]
    assert not leftovers


def test_write_env_token_appends_when_var_missing(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("META_AD_ACCOUNT_ID=act_123\n")
    monkeypatch.setattr(api, "BASE_DIR", str(tmp_path))

    _write_env_token("newtoken")
    assert "META_ACCESS_TOKEN=newtoken" in env.read_text()

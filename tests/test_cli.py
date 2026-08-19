"""End-to-end CLI behavior via subprocess (the student's first-contact paths)."""

import os
import subprocess
import sys

from metaads import __version__

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(APP_DIR, "meta_ads_cli.py")


def _run(*argv, token="your-long-lived-token-here"):
    """Run the CLI with placeholder credentials (dotenv never overrides os.environ)."""
    env = dict(os.environ)
    env["META_ACCESS_TOKEN"] = token
    env["META_AD_ACCOUNT_ID"] = "act_XXXXXXXXX"
    return subprocess.run(
        [sys.executable, CLI, *argv],
        capture_output=True, text=True, env=env, cwd=APP_DIR, timeout=60,
    )


def test_help_works_without_configured_env():
    r = _run("--help")
    assert r.returncode == 0
    assert "usage:" in r.stdout


def test_version_works_without_configured_env():
    r = _run("--version")
    assert r.returncode == 0
    assert __version__ in r.stdout


def test_subcommand_help_works_without_configured_env():
    r = _run("campaign-create", "--help")
    assert r.returncode == 0
    assert "--confirm" in r.stdout


def test_real_command_fails_cleanly_without_token():
    r = _run("campaigns")
    assert r.returncode == 1
    assert "META_ACCESS_TOKEN not set" in r.stderr
    assert "Traceback" not in r.stderr


def test_json_flag_suppresses_banner_noise():
    """Even the error path with --json must keep stdout empty (parseable)."""
    r = _run("campaigns", "--json")
    assert r.returncode == 1
    assert r.stdout.strip() == ""

"""Command-layer tests: clean errors instead of tracebacks, JSON output contracts."""

import json
from argparse import Namespace

import pytest

from metaads import api
from metaads.commands import common, creatives, insights
from metaads.commands.adsets import build_targeting
from metaads.commands.campaigns import cmd_campaign_create
from metaads.commands.common import parse_genders, parse_json_arg


# ---------------------------------------------------------------------------
# Novice-proof flag parsing
# ---------------------------------------------------------------------------

def test_parse_json_arg_bad_json_dies_cleanly(capsys):
    with pytest.raises(SystemExit):
        parse_json_arg("{bad json", "--targeting")
    err = capsys.readouterr().err
    assert "--targeting is not valid JSON" in err
    assert "Traceback" not in err


def test_parse_json_arg_none_passthrough():
    assert parse_json_arg(None, "--x") is None
    assert parse_json_arg('["HOUSING"]', "--x") == ["HOUSING"]


def test_parse_genders_word_dies_with_hint(capsys):
    with pytest.raises(SystemExit):
        parse_genders("male")
    assert "1 (male)" in capsys.readouterr().err


def test_parse_genders_valid():
    assert parse_genders("1,2") == [1, 2]
    assert parse_genders(" 2 ") == [2]


def test_build_targeting_genders_via_args(capsys):
    args = Namespace(countries="CZ", age_min=None, age_max=None, genders="female",
                     advantage_audience=None, publisher_platforms=None,
                     facebook_positions=None, instagram_positions=None,
                     device_platforms=None)
    with pytest.raises(SystemExit):
        build_targeting(args)
    assert "Traceback" not in capsys.readouterr().err


def test_campaign_create_bad_special_ad_categories_dies_before_api(monkeypatch, capsys):
    def fail(*a, **k):
        raise AssertionError("mutate must not be reached with invalid JSON flag")

    monkeypatch.setattr(api, "mutate", fail)
    args = Namespace(name="T", objective="OUTCOME_TRAFFIC", status="PAUSED",
                     special_ad_categories="not-json", daily_budget=None,
                     lifetime_budget=None, adset_budget_sharing=None,
                     bid_strategy=None, start_time=None, stop_time=None,
                     confirm=False, json=False, account_id=None)
    with pytest.raises(SystemExit):
        cmd_campaign_create(args)
    assert "not valid JSON" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# delete --json emits valid JSON in every branch
# ---------------------------------------------------------------------------

def _delete_args(**over):
    base = dict(confirm=False, json=True, force=False)
    base.update(over)
    return Namespace(**base)


def test_delete_dry_run_json_is_valid_json(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", lambda *a, **k: {
        "name": "Kampan", "status": "ACTIVE", "effective_status": "ACTIVE"})
    common.delete_with_brake("campaign", "123", _delete_args())
    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is False
    assert out["would_delete"]["id"] == "123"


def test_delete_confirm_json_reports_executed(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", lambda method, *a, **k: {
        "name": "K", "status": "PAUSED", "effective_status": "PAUSED"})
    common.delete_with_brake("campaign", "123", _delete_args(confirm=True))
    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is True and out["deleted"] == "123"


def test_creative_delete_dry_run_json(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", lambda *a, **k: {"name": "C", "status": "ACTIVE"})
    creatives.cmd_creative_delete(Namespace(creative_id="55", confirm=False, json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is False
    assert out["would_delete"]["kind"] == "creative"


def test_delete_brake_refuses_active_without_force(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", lambda method, *a, **k: {
        "name": "K", "status": "ACTIVE", "effective_status": "ACTIVE"})
    with pytest.raises(SystemExit):
        common.delete_with_brake("campaign", "123", _delete_args(confirm=True, json=False))
    assert "refusing" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Insights: dead attribution windows, robust formatting
# ---------------------------------------------------------------------------

def test_attribution_windows_reflect_2026_api_state():
    assert "7d_view" not in insights.ATTRIBUTION_WINDOWS
    assert "28d_view" not in insights.ATTRIBUTION_WINDOWS
    assert "1d_ev" in insights.ATTRIBUTION_WINDOWS


def _insight_args(**over):
    base = dict(fields=None, date_from=None, date_to=None, date_preset=None,
                level=None, breakdowns=None, action_breakdowns=None,
                time_increment=None, attribution_windows=None,
                unified_attribution=False, filtering=None, sort=None)
    base.update(over)
    return Namespace(**base)


def test_removed_attribution_window_dies_with_explanation(capsys):
    with pytest.raises(SystemExit):
        insights.build_insight_params(_insight_args(attribution_windows="7d_view"))
    assert "removed by Meta" in capsys.readouterr().err


def test_valid_attribution_windows_pass():
    params = insights.build_insight_params(
        _insight_args(attribution_windows="7d_click,1d_ev"))
    assert json.loads(params["action_attribution_windows"]) == ["7d_click", "1d_ev"]


def test_format_insight_value_never_crashes():
    f = insights._format_insight_value
    assert f("spend", None) == "---"
    assert f("spend", "12.5") == "12.50"
    assert f("impressions", "not-a-number") == "not-a-number"
    assert "?" in f("actions", [{"action_type": "lead"}])  # missing 'value' key
    assert f("cost_per_action_type", [{"action_type": "lead", "value": None}])

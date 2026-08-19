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


# ---------------------------------------------------------------------------
# creative-clone: url_tags carry-over, enhancement opt-out (issues #1, #2)
# ---------------------------------------------------------------------------

def _clone_args(**over):
    base = dict(creative_id="777", name="Clone", swap_video=None, swap_thumbnail=None,
                swap_image=None, new_url=None, url_tags=None, no_enhancements=False,
                swap_on_ad=None, confirm=False, json=True, account_id=None)
    base.update(over)
    return Namespace(**base)


def _clone_source(**over):
    base = {
        "object_story_spec": {"page_id": "1"},
        "asset_feed_spec": {"images": [{"hash": "abc"}]},
        "url_tags": "utm_source=fb&utm_content={{ad.name}}",
    }
    base.update(over)
    return base


def _run_clone(monkeypatch, source, args):
    captured = {}
    monkeypatch.setattr(api, "_api_call", lambda m, e, p=None, **k: source)
    monkeypatch.setattr(api, "mutate",
                        lambda endpoint, params, confirm, **k: captured.update(params) or ({}, False))
    creatives.cmd_creative_clone(args)
    return captured


def test_clone_reads_and_carries_url_tags(monkeypatch, capsys):
    fields_seen = {}

    def fake_get(method, endpoint, params=None, **k):
        fields_seen.update(params or {})
        return _clone_source()

    captured = {}
    monkeypatch.setattr(api, "_api_call", fake_get)
    monkeypatch.setattr(api, "mutate",
                        lambda endpoint, params, confirm, **k: captured.update(params) or ({}, False))
    creatives.cmd_creative_clone(_clone_args())
    assert "url_tags" in fields_seen["fields"]
    assert captured["url_tags"] == "utm_source=fb&utm_content={{ad.name}}"


def test_clone_url_tags_flag_overrides_source(monkeypatch, capsys):
    captured = _run_clone(monkeypatch, _clone_source(),
                          _clone_args(url_tags="utm_source=override"))
    assert captured["url_tags"] == "utm_source=override"


def test_clone_without_source_url_tags_omits_key(monkeypatch, capsys):
    captured = _run_clone(monkeypatch, _clone_source(url_tags=None), _clone_args())
    assert "url_tags" not in captured


def test_clone_no_enhancements_replaces_source_dof(monkeypatch, capsys):
    source = _clone_source(degrees_of_freedom_spec={
        "creative_features_spec": {"image_touchups": {"enroll_status": "OPT_IN"}}})
    captured = _run_clone(monkeypatch, source, _clone_args(no_enhancements=True))
    spec = json.loads(captured["degrees_of_freedom_spec"])["creative_features_spec"]
    assert all(v == {"enroll_status": "OPT_OUT"} for v in spec.values())
    assert len(spec) == len(creatives.ENHANCEMENT_FEATURES)


def test_clone_preserves_source_dof_by_default(monkeypatch, capsys):
    source = _clone_source(degrees_of_freedom_spec={
        "creative_features_spec": {"image_touchups": {"enroll_status": "OPT_IN"}}})
    captured = _run_clone(monkeypatch, source, _clone_args())
    spec = json.loads(captured["degrees_of_freedom_spec"])["creative_features_spec"]
    assert spec["image_touchups"] == {"enroll_status": "OPT_IN"}


# ---------------------------------------------------------------------------
# creative-create / from-post / from-ig: --no-enhancements (issue #2)
# ---------------------------------------------------------------------------

def _create_args(**over):
    base = dict(name="C", type="link", page_id="123", message="Hi", link="https://ex.cz",
                headline=None, description=None, image_hash=None, image_url=None,
                video_id=None, video_thumbnail=None, call_to_action=None,
                child_attachments=None, url_tags=None, no_enhancements=False,
                confirm=False, json=True, account_id=None)
    base.update(over)
    return Namespace(**base)


def test_enhancement_feature_list_is_the_14_opt_out_set():
    assert len(creatives.ENHANCEMENT_FEATURES) == 14
    assert len(set(creatives.ENHANCEMENT_FEATURES)) == 14
    # deprecated features the API rejects on create must never be sent
    assert not set(creatives.ENHANCEMENT_FEATURES) & set(creatives._DOF_DEPRECATED)


def test_creative_create_no_enhancements_sends_full_opt_out(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(api, "mutate",
                        lambda endpoint, params, confirm, **k: captured.update(params) or ({}, False))
    creatives.cmd_creative_create(_create_args(no_enhancements=True))
    spec = json.loads(captured["degrees_of_freedom_spec"])["creative_features_spec"]
    assert set(spec) == set(creatives.ENHANCEMENT_FEATURES)
    assert all(v == {"enroll_status": "OPT_OUT"} for v in spec.values())


def test_creative_create_default_sends_no_dof(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(api, "mutate",
                        lambda endpoint, params, confirm, **k: captured.update(params) or ({}, False))
    creatives.cmd_creative_create(_create_args())
    assert "degrees_of_freedom_spec" not in captured


def test_creative_from_post_no_enhancements(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(api, "mutate",
                        lambda endpoint, params, confirm, **k: captured.update(params) or ({}, False))
    creatives.cmd_creative_from_post(Namespace(
        post_id="1_2", page_id=None, name="P", call_to_action=None, link=None,
        no_enhancements=True, confirm=False, json=True, account_id=None))
    assert "degrees_of_freedom_spec" in captured


def test_creative_from_ig_no_enhancements(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(api, "mutate",
                        lambda endpoint, params, confirm, **k: captured.update(params) or ({}, False))
    creatives.cmd_creative_from_ig(Namespace(
        media_id="55", page_id="123", ig_user_id="99", name="I", call_to_action=None,
        link=None, no_enhancements=True, confirm=False, json=True, account_id=None))
    assert "degrees_of_freedom_spec" in captured


# ---------------------------------------------------------------------------
# insights: loud truncation instead of silently incomplete numbers (issue #3)
# ---------------------------------------------------------------------------

def test_insights_warns_on_paging_next(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", lambda *a, **k: {
        "data": [{"spend": "1"}],
        "paging": {"cursors": {}, "next": "https://graph.facebook.com/next"},
    })
    insights.cmd_insights(_insight_args(object_id=None, json=True, limit=100,
                                        account_id=None))
    captured = capsys.readouterr()
    assert "Truncated" in captured.err
    assert json.loads(captured.out) == [{"spend": "1"}]  # stdout stays clean JSON


def test_insights_no_warning_without_paging_next(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", lambda *a, **k: {
        "data": [{"spend": "1"}], "paging": {"cursors": {}},
    })
    insights.cmd_insights(_insight_args(object_id=None, json=True, limit=100,
                                        account_id=None))
    assert "Truncated" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# creative-create --type flex: asset_feed_spec from scratch (issue #3)
# ---------------------------------------------------------------------------

def _flex_args(**over):
    base = dict(name="F", type="flex", page_id="123", link="https://ex.cz",
                message=["Text A", "Text B"], headline=["Titulek"], description=None,
                image_hash=["h1", "h2"], image_url=None, video_id=None,
                video_thumbnail=None, call_to_action="LEARN_MORE",
                child_attachments=None, url_tags=None, lead_gen_form_id=None,
                ig_user_id=None, no_enhancements=True,
                confirm=False, json=True, account_id=None)
    base.update(over)
    return Namespace(**base)


def _capture_create(monkeypatch, args):
    captured = {}
    monkeypatch.setattr(api, "mutate",
                        lambda endpoint, params, confirm, **k: captured.update(params) or ({}, False))
    creatives.cmd_creative_create(args)
    return captured


def test_flex_builds_asset_feed_spec(monkeypatch, capsys):
    captured = _capture_create(monkeypatch, _flex_args())
    afs = json.loads(captured["asset_feed_spec"])
    assert [b["text"] for b in afs["bodies"]] == ["Text A", "Text B"]
    assert [t["text"] for t in afs["titles"]] == ["Titulek"]
    assert [i["hash"] for i in afs["images"]] == ["h1", "h2"]
    assert afs["link_urls"] == [{"website_url": "https://ex.cz"}]
    assert afs["ad_formats"] == ["SINGLE_IMAGE"]
    assert afs["call_to_action_types"] == ["LEARN_MORE"]
    oss = json.loads(captured["object_story_spec"])
    assert oss == {"page_id": "123"}
    assert "degrees_of_freedom_spec" in captured  # no_enhancements=True


def test_flex_video_variant(monkeypatch, capsys):
    captured = _capture_create(monkeypatch, _flex_args(
        image_hash=None, video_id=["v1", "v2"], video_thumbnail="https://ex.cz/t.jpg"))
    afs = json.loads(captured["asset_feed_spec"])
    assert afs["videos"] == [
        {"video_id": "v1", "thumbnail_url": "https://ex.cz/t.jpg"},
        {"video_id": "v2", "thumbnail_url": "https://ex.cz/t.jpg"},
    ]
    assert afs["ad_formats"] == ["SINGLE_VIDEO"]
    assert "images" not in afs


def test_flex_mixed_media_gets_both_ad_formats(monkeypatch, capsys):
    captured = _capture_create(monkeypatch, _flex_args(video_id=["v1"]))
    afs = json.loads(captured["asset_feed_spec"])
    assert afs["ad_formats"] == ["SINGLE_IMAGE", "SINGLE_VIDEO"]


def test_flex_ig_user_id_lands_in_object_story_spec(monkeypatch, capsys):
    captured = _capture_create(monkeypatch, _flex_args(ig_user_id="998877"))
    assert json.loads(captured["object_story_spec"])["instagram_user_id"] == "998877"


def test_flex_page_only_without_dof_hints_pbia(monkeypatch, capsys):
    _capture_create(monkeypatch, _flex_args(no_enhancements=False))
    assert "1772103" in capsys.readouterr().err


def test_flex_requires_media(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _capture_create(monkeypatch, _flex_args(image_hash=None, video_id=None))
    assert "needs media" in capsys.readouterr().err


def test_flex_rejects_more_than_five_texts(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _capture_create(monkeypatch, _flex_args(message=[f"T{i}" for i in range(6)]))
    assert "max 5" in capsys.readouterr().err


def test_flex_rejects_duplicate_hashes(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _capture_create(monkeypatch, _flex_args(image_hash=["h1", "h1"]))
    assert "1815629" in capsys.readouterr().err


def test_non_flex_rejects_repeated_message(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _capture_create(monkeypatch, _create_args(message=["A", "B"]))
    assert "only supported with --type flex" in capsys.readouterr().err


def test_single_string_args_still_work(monkeypatch, capsys):
    """Backward compat: scripts passing plain strings (pre-append) keep working."""
    captured = _capture_create(monkeypatch, _create_args(message="Hi", headline="H"))
    link_data = json.loads(captured["object_story_spec"])["link_data"]
    assert link_data["message"] == "Hi" and link_data["name"] == "H"


# ---------------------------------------------------------------------------
# creative-create --lead-gen-form-id (issue #3)
# ---------------------------------------------------------------------------

def test_lead_gen_link_creative_defaults_to_sign_up(monkeypatch, capsys):
    captured = _capture_create(monkeypatch, _create_args(
        lead_gen_form_id="42", link="http://fb.me/", call_to_action=None))
    cta = json.loads(captured["object_story_spec"])["link_data"]["call_to_action"]
    assert cta == {"type": "SIGN_UP", "value": {"lead_gen_form_id": "42"}}


def test_lead_gen_video_creative_keeps_link_in_value(monkeypatch, capsys):
    captured = _capture_create(monkeypatch, _create_args(
        type="video", video_id="v9", lead_gen_form_id="42",
        link="http://fb.me/", call_to_action="SUBSCRIBE"))
    cta = json.loads(captured["object_story_spec"])["video_data"]["call_to_action"]
    assert cta["type"] == "SUBSCRIBE"
    assert cta["value"] == {"link": "http://fb.me/", "lead_gen_form_id": "42"}


def test_lead_gen_requires_link_with_fbme_hint(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _capture_create(monkeypatch, _create_args(lead_gen_form_id="42", link=None))
    err = capsys.readouterr().err
    assert "2061015" in err and "fb.me" in err


def test_lead_gen_rejected_on_flex(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _capture_create(monkeypatch, _flex_args(lead_gen_form_id="42"))
    assert "link/video only" in capsys.readouterr().err

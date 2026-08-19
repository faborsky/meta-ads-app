"""Engine tests: token hygiene, rate-limit guard, retry policy, dry-run mutations."""

import json

import pytest
import requests

from metaads import api


# ---------------------------------------------------------------------------
# Token hygiene
# ---------------------------------------------------------------------------

def test_redact_strips_all_secret_params():
    text = ("https://graph.facebook.com/v25.0/act_1/campaigns?access_token=EAAsecret1"
            "&fields=id&client_secret=abc123&fb_exchange_token=EAAsecret2&input_token=EAAsecret3")
    redacted = api._redact(text)
    for secret in ("EAAsecret1", "abc123", "EAAsecret2", "EAAsecret3"):
        assert secret not in redacted
    assert "access_token=REDACTED" in redacted
    assert "fields=id" in redacted  # non-secrets survive


def test_api_call_sends_bearer_header_not_query_param(monkeypatch, dummy_resp):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(url=url, params=params, headers=headers)
        return dummy_resp({"data": []})

    monkeypatch.setattr(api.requests, "get", fake_get)
    api._api_call("GET", "act_1000000/campaigns", {"fields": "id"})

    assert captured["headers"]["Authorization"] == "Bearer FAKETESTTOKEN123"
    assert "access_token" not in captured["params"]
    assert "access_token" not in captured["url"]


def test_api_call_does_not_mutate_caller_params(monkeypatch, dummy_resp):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: dummy_resp({"data": []}))
    params = {"fields": "id"}
    api._api_call("GET", "act_1000000/campaigns", params)
    assert params == {"fields": "id"}


def test_connection_error_output_is_redacted(monkeypatch, capsys):
    def fake_get(url, params=None, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError(
            "Max retries exceeded with url: /v25.0/act_1/campaigns?access_token=EAAsupersecret&fields=id"
        )

    monkeypatch.setattr(api.requests, "get", fake_get)
    with pytest.raises(SystemExit):
        api._api_call("GET", "act_1000000/campaigns", {"fields": "id"})
    err = capsys.readouterr().err
    assert "EAAsupersecret" not in err
    assert "REDACTED" in err


def test_non_graph_url_is_refused(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: called.append(1))
    with pytest.raises(SystemExit):
        api._api_call("GET", "https://evil.example.com/steal?access_token=EAAx")
    assert not called
    err = capsys.readouterr().err
    assert "EAAx" not in err  # redacted even in the refusal message


def test_token_cache_stores_hash_not_token_material(monkeypatch, dummy_resp, tmp_path):
    monkeypatch.setattr(
        api.requests, "get",
        lambda *a, **k: dummy_resp({"data": {"expires_at": 0}}),
    )
    api.maybe_warn_token_expiry()
    cache = json.loads(open(api._token_cache_file()).read())
    assert "token_tail" not in cache
    assert "FAKETESTTOKEN123" not in json.dumps(cache)
    assert cache["token_hash"] == api._token_fingerprint()


# ---------------------------------------------------------------------------
# Usage guard (hard stop)
# ---------------------------------------------------------------------------

def _write_usage(pct, account="1000000"):
    api._save_usage({account: {"pct": pct, "ts": api.time.time(), "tier": "development_access"}})


def test_buc_max_across_entries_survives_low_entry(monkeypatch, dummy_resp):
    """A second, cooler BUC entry must not overwrite the hot one (guard bypass)."""
    header = json.dumps({"1000000": [
        {"type": "ads_management", "call_count": 96, "total_cputime": 10, "total_time": 10},
        {"type": "ads_insights", "call_count": 10, "total_cputime": 5, "total_time": 5},
    ]})
    monkeypatch.setattr(
        api.requests, "get",
        lambda *a, **k: dummy_resp({"data": []}, headers={"X-Business-Use-Case-Usage": header}),
    )
    api._api_call("GET", "act_1000000/campaigns", {}, _skip_guard=True)
    usage = api._load_usage()
    assert usage["1000000"]["pct"] == 96


def test_buc_non_ads_entries_ignored(monkeypatch, dummy_resp):
    header = json.dumps({"99999": [{"type": "pages", "call_count": 99}]})
    monkeypatch.setattr(
        api.requests, "get",
        lambda *a, **k: dummy_resp({"data": []}, headers={"X-Business-Use-Case-Usage": header}),
    )
    api._api_call("GET", "act_1000000/campaigns", {}, _skip_guard=True)
    assert "99999" not in api._load_usage()


def test_usage_guard_blocks_hot_account(capsys):
    _write_usage(96)
    with pytest.raises(SystemExit):
        api._usage_guard("act_1000000/campaigns")
    assert "hard stop" in capsys.readouterr().err


def test_usage_guard_does_not_block_other_account():
    _write_usage(96, account="1000000")
    api._usage_guard("act_2000000/campaigns")  # must not raise


def test_usage_guard_conservative_without_account_in_endpoint():
    _write_usage(96)
    with pytest.raises(SystemExit):
        api._usage_guard("123456789")  # bare object ID → conservative block


def test_usage_guard_ignores_stale_reading():
    api._save_usage({"1000000": {"pct": 96, "ts": api.time.time() - 3600, "tier": None}})
    api._usage_guard("act_1000000/campaigns")  # stale (>10 min) → no block


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

def _transient_error_resp(dummy_resp):
    return dummy_resp({"error": {
        "code": 2, "error_subcode": 0, "message": "An unexpected error has occurred.",
        "is_transient": True,
    }})


def test_transient_post_is_never_retried(monkeypatch, dummy_resp, capsys):
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None, files=None):
        calls.append(1)
        return _transient_error_resp(dummy_resp)

    monkeypatch.setattr(api.requests, "post", fake_post)
    with pytest.raises(SystemExit):
        api._api_call("POST", "act_1000000/campaigns", {"name": "x"})
    assert len(calls) == 1  # exactly one attempt — no duplicate campaign risk
    assert "MAY have landed" in capsys.readouterr().err


def test_transient_get_is_retried(monkeypatch, dummy_resp):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _transient_error_resp(dummy_resp)
        return dummy_resp({"data": [{"id": "1"}]})

    monkeypatch.setattr(api.requests, "get", fake_get)
    data = api._api_call("GET", "act_1000000/campaigns", {})
    assert len(calls) == 2
    assert data["data"][0]["id"] == "1"


def test_rate_limit_with_long_regain_estimate_dies_instead_of_sleeping(
        monkeypatch, dummy_resp, capsys):
    header = json.dumps({"1000000": [
        {"type": "ads_management", "call_count": 100, "estimated_time_to_regain_access": 120},
    ]})
    slept = []
    monkeypatch.setattr(api.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(
        api.requests, "get",
        lambda *a, **k: dummy_resp(
            {"error": {"code": 80004, "message": "throttled", "is_transient": False}},
            headers={"X-Business-Use-Case-Usage": header},
        ),
    )
    with pytest.raises(SystemExit):
        api._api_call("GET", "act_1000000/campaigns", {}, _skip_guard=True)
    assert not [s for s in slept if s > 300]
    assert "min" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Dry-run mutations
# ---------------------------------------------------------------------------

def test_mutate_dry_run_adds_validate_only(monkeypatch):
    captured = {}

    def fake_call(method, endpoint, params=None, **kwargs):
        captured.update(method=method, endpoint=endpoint, params=params)
        return {"success": True}

    monkeypatch.setattr(api, "_api_call", fake_call)
    data, executed = api.mutate("act_1/campaigns", {"name": "x"}, confirm=False)
    assert not executed
    assert json.loads(captured["params"]["execution_options"]) == ["validate_only"]


def test_mutate_confirm_sends_clean_write(monkeypatch):
    captured = {}
    monkeypatch.setattr(api, "_api_call",
                        lambda m, e, p=None, **k: captured.update(params=p) or {"id": "123"})
    data, executed = api.mutate("act_1/campaigns", {"name": "x"}, confirm=True)
    assert executed
    assert "execution_options" not in captured["params"]


def test_mutate_unsupported_dry_run_makes_no_call(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no API call may happen on /copies dry-run")

    monkeypatch.setattr(api, "_api_call", fail)
    data, executed = api.mutate("123/copies", {"status_option": "PAUSED"},
                                confirm=False, validate_supported=False)
    assert data is None and executed is False


# ---------------------------------------------------------------------------
# Token expiry probe must never kill a command
# ---------------------------------------------------------------------------

def test_token_probe_network_failure_is_silent_and_cached(monkeypatch):
    def fake_get(*a, **k):
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(api.requests, "get", fake_get)
    api.maybe_warn_token_expiry()  # must not raise
    cache = json.loads(open(api._token_cache_file()).read())
    assert cache["check_failed"] is True


def test_token_probe_api_error_is_silent(monkeypatch, dummy_resp):
    monkeypatch.setattr(
        api.requests, "get",
        lambda *a, **k: dummy_resp({"error": {"code": 100, "message": "nope"}}),
    )
    api.maybe_warn_token_expiry()  # must not raise
    cache = json.loads(open(api._token_cache_file()).read())
    assert cache["check_failed"] is True


def test_expired_token_warns_with_expired_wording(monkeypatch, capsys):
    api._write_json_atomic(api._token_cache_file(), {
        "token_hash": api._token_fingerprint(),
        "checked_at": api.time.time(),
        "expires_at": api.time.time() - 5 * 86400,
    })
    api.maybe_warn_token_expiry()
    err = capsys.readouterr().err
    assert "expired" in err
    assert "-" not in err.split("day(s)")[0].split()[-1]  # no "expires in -5 day(s)"


# ---------------------------------------------------------------------------
# Budget currency guard
# ---------------------------------------------------------------------------

def test_budget_guard_refuses_zero_decimal_currency(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", lambda *a, **k: {"currency": "JPY"})
    with pytest.raises(SystemExit):
        api.budget_currency_guard("act_1000000")
    assert "JPY" in capsys.readouterr().err


def test_budget_guard_passes_and_caches_offset100_currency(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_api_call",
                        lambda *a, **k: calls.append(1) or {"currency": "CZK"})
    api.budget_currency_guard("act_1000000")
    api.budget_currency_guard("act_1000000")  # second call served from cache
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Idempotent-write retry opt-in (chunked upload transfer)
# ---------------------------------------------------------------------------

def test_transient_post_retried_when_explicitly_idempotent(monkeypatch, dummy_resp):
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None, files=None):
        calls.append(1)
        if len(calls) == 1:
            return _transient_error_resp(dummy_resp)
        return dummy_resp({"start_offset": "100", "end_offset": "100"})

    monkeypatch.setattr(api.requests, "post", fake_post)
    data = api._api_call("POST", "act_1000000/advideos",
                         {"upload_phase": "transfer"}, retry_transient_writes=True)
    assert len(calls) == 2
    assert data["start_offset"] == "100"


def test_non_json_413_explains_payload_too_large(monkeypatch, capsys):
    class Raw:
        headers = {}
        status_code = 413
        text = ""

        def json(self):
            raise json.JSONDecodeError("x", "", 0)

    monkeypatch.setattr(api.requests, "post", lambda *a, **k: Raw())
    with pytest.raises(SystemExit):
        api._api_call("POST", "act_1000000/advideos", {})
    err = capsys.readouterr().err
    assert "413" in err
    assert "too large" in err

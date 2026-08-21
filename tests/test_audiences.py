"""Custom-audience tests.

The load-bearing ones are the hashing tests: uploading a customer list through
the API (unlike the Ads Manager UI) means WE hash, and a regression here would
put raw personal data on the wire.
"""

import csv
import hashlib
import json
import re
from argparse import Namespace

import pytest

from metaads import api
from metaads.commands import audiences
from metaads.commands.audiences import (
    build_users_payload,
    build_website_rule,
    cmd_audience_create_customerlist,
    cmd_audience_create_website,
    load_customer_csv,
    normalize_email,
    normalize_phone,
)

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _write_csv(tmp_path, rows, headers=("email", "phone")):
    path = tmp_path / "customers.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        w.writerows(rows)
    return str(path)


# ---------------------------------------------------------------------------
# Normalisation — Meta's documented rules
# ---------------------------------------------------------------------------

def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  Mary@Example.COM  ") == "mary@example.com"


def test_normalize_email_rejects_junk():
    for bad in ("", None, "   ", "not-an-email", "@example.com", "mary@"):
        assert normalize_email(bad) is None


def test_normalize_phone_strips_symbols_and_keeps_country_code():
    assert normalize_phone("+380 (67) 123-45-67") == "380671234567"


def test_normalize_phone_drops_leading_zero_and_adds_country_code():
    assert normalize_phone("067 123 45 67", "380") == "380671234567"


def test_normalize_phone_does_not_double_prefix_country_code():
    # Already carries 380 without a '+' — prepending again would corrupt it.
    assert normalize_phone("380671234567", "380") == "380671234567"


def test_normalize_phone_handles_00_international_prefix():
    assert normalize_phone("00380671234567") == "380671234567"


def test_normalize_phone_rejects_too_short():
    assert normalize_phone("12345") is None
    assert normalize_phone("", "380") is None


# ---------------------------------------------------------------------------
# Hashing — the safety-critical contract
# ---------------------------------------------------------------------------

def test_payload_values_are_all_sha256():
    records = [{"EMAIL": "mary@example.com", "PHONE": "380671234567"}]
    payload = build_users_payload(records, ["EMAIL", "PHONE"])
    for row in payload["data"]:
        for value in row:
            assert SHA256_HEX.match(value), f"not a SHA256 hex digest: {value!r}"


def test_payload_matches_meta_documented_hash():
    # Reference digest from Meta's customer-file docs.
    payload = build_users_payload([{"EMAIL": "mary@example.com"}], ["EMAIL"])
    assert payload["data"][0][0] == \
        "f1904cf1a9d73a55fa5de0ac823c4403ded71afd4c3248d00bdcd0866552bb79"


def test_no_raw_pii_anywhere_in_payload():
    """The regression guard: raw identifiers must not survive into the payload."""
    records = [
        {"EMAIL": "mary@example.com", "PHONE": "380671234567"},
        {"EMAIL": "john.doe@gmail.com"},
        {"PHONE": "420777123456"},
    ]
    payload = build_users_payload(records, ["EMAIL", "PHONE"])
    blob = json.dumps(payload)
    for raw in ("mary@example.com", "example.com", "380671234567",
                "john.doe@gmail.com", "gmail.com", "420777123456"):
        assert raw not in blob, f"raw PII leaked into payload: {raw}"


def test_missing_value_becomes_empty_not_hash_of_empty():
    payload = build_users_payload([{"EMAIL": "mary@example.com"}], ["EMAIL", "PHONE"])
    assert payload["data"][0][1] == ""
    # hash("") must never be sent — it would match every other empty record.
    assert payload["data"][0][1] != hashlib.sha256(b"").hexdigest()


def test_schema_order_matches_data_columns():
    records = [{"EMAIL": "a@b.com", "PHONE": "380671234567"}]
    payload = build_users_payload(records, ["PHONE", "EMAIL"])
    assert payload["schema"] == ["PHONE", "EMAIL"]
    assert payload["data"][0][0] == hashlib.sha256(b"380671234567").hexdigest()
    assert payload["data"][0][1] == hashlib.sha256(b"a@b.com").hexdigest()


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def test_load_csv_normalises_and_counts(tmp_path):
    path = _write_csv(tmp_path, [
        ["  Mary@Example.COM ", "+380671234567"],
        ["john@example.com", "067 123 45 68"],
        ["broken-email", ""],
    ])
    records, schema, stats = load_customer_csv(path, default_country_code="380")
    assert schema == ["EMAIL", "PHONE"]
    assert stats["rows"] == 3
    assert stats["uploadable"] == 2
    assert stats["skipped"] == 1
    assert records[0]["EMAIL"] == "mary@example.com"
    assert records[1]["PHONE"] == "380671234568"


def test_load_csv_accepts_column_aliases(tmp_path):
    path = _write_csv(tmp_path, [["mary@example.com"]], headers=("E-Mail",))
    records, schema, _ = load_customer_csv(path)
    assert schema == ["EMAIL"]
    assert records[0]["EMAIL"] == "mary@example.com"


def test_load_csv_without_known_column_dies_cleanly(tmp_path, capsys):
    path = _write_csv(tmp_path, [["x"]], headers=("first_name",))
    with pytest.raises(SystemExit):
        load_customer_csv(path)
    err = capsys.readouterr().err
    assert "no email/phone column" in err
    assert "first_name" in err
    assert "Traceback" not in err


def test_load_csv_missing_file_dies_cleanly(tmp_path, capsys):
    with pytest.raises(SystemExit):
        load_customer_csv(str(tmp_path / "nope.csv"))
    assert "CSV not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Website rule shape
# ---------------------------------------------------------------------------

def test_website_rule_shape_matches_flexible_spec():
    rule = build_website_rule("PIX1", 2592000, event="InitiateCheckout",
                              exclude_event="Purchase")
    inc = rule["inclusions"]["rules"][0]
    assert inc["event_sources"] == [{"id": "PIX1", "type": "pixel"}]
    assert inc["retention_seconds"] == 2592000
    assert inc["filter"]["filters"][0] == {
        "field": "event", "operator": "eq", "value": "InitiateCheckout"}
    exc = rule["exclusions"]["rules"][0]
    assert exc["filter"]["filters"][0]["value"] == "Purchase"


def test_website_rule_omits_exclusions_when_not_asked():
    rule = build_website_rule("PIX1", 86400, event="AddToCart")
    assert "exclusions" not in rule


# ---------------------------------------------------------------------------
# Dry-run contract: no write without --confirm
# ---------------------------------------------------------------------------

def _boom(*a, **k):
    raise AssertionError("dry-run must not call the API")


def test_website_dry_run_makes_no_api_call(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", _boom)
    cmd_audience_create_website(Namespace(
        account_id="act_1000000", name="ICO no purchase 30d", pixel_id="PIX1",
        event="InitiateCheckout", exclude_event="Purchase", url_contains=None,
        days=30, prefill=1, description=None, confirm=False, json=False))
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "Add --confirm to execute." in out


def test_customerlist_dry_run_makes_no_api_call_and_masks_pii(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(api, "_api_call", _boom)
    path = _write_csv(tmp_path, [["mary@example.com", "+380671234567"]])
    cmd_audience_create_customerlist(Namespace(
        account_id="act_1000000", name="Buyers", csv=path, country_code=None,
        customer_file_source="USER_PROVIDED_ONLY", preview=3, description=None,
        confirm=False, json=False))
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "mary@example.com" not in out       # masked, never printed in full
    assert "380671234567" not in out
    assert "m***@example.com" in out


def test_customerlist_confirm_uploads_hashed_batches(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_api(method, endpoint, params=None, **kw):
        calls.append((method, endpoint, params or {}))
        return {"id": "AUD123"}

    monkeypatch.setattr(api, "_api_call", fake_api)
    monkeypatch.setattr(audiences, "USERS_BATCH_SIZE", 2)
    path = _write_csv(tmp_path, [
        ["a@x.com", "+380671234561"],
        ["b@x.com", "+380671234562"],
        ["c@x.com", "+380671234563"],
    ])
    cmd_audience_create_customerlist(Namespace(
        account_id="act_1000000", name="Buyers", csv=path, country_code=None,
        customer_file_source="USER_PROVIDED_ONLY", preview=3, description=None,
        confirm=True, json=False))

    create = calls[0]
    assert create[1] == "act_1000000/customaudiences"
    assert create[2]["subtype"] == "CUSTOM"
    assert create[2]["customer_file_source"] == "USER_PROVIDED_ONLY"

    uploads = [c for c in calls if c[1] == "AUD123/users"]
    assert len(uploads) == 2                      # 3 records, batch size 2
    assert json.loads(uploads[-1][2]["session"])["last_batch_flag"] is True

    for _, _, params in uploads:
        payload = json.loads(params["payload"])
        for row in payload["data"]:
            for value in row:
                assert SHA256_HEX.match(value)
        assert "a@x.com" not in params["payload"]
        assert "380671234561" not in params["payload"]


def test_customerlist_empty_after_normalisation_dies(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(api, "_api_call", _boom)
    path = _write_csv(tmp_path, [["junk", "1"]])
    with pytest.raises(SystemExit):
        cmd_audience_create_customerlist(Namespace(
            account_id="act_1000000", name="X", csv=path, country_code=None,
            customer_file_source="USER_PROVIDED_ONLY", preview=3, description=None,
            confirm=False, json=False))
    assert "no usable email/phone rows" in capsys.readouterr().err


def test_website_days_out_of_range_dies_before_api(monkeypatch, capsys):
    monkeypatch.setattr(api, "_api_call", _boom)
    with pytest.raises(SystemExit):
        cmd_audience_create_website(Namespace(
            account_id="act_1000000", name="X", pixel_id="PIX1",
            event="Purchase", exclude_event=None, url_contains=None,
            days=400, prefill=1, description=None, confirm=False, json=False))
    assert "--days must be between 1 and 365" in capsys.readouterr().err

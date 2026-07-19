"""Commands: token-info, token-extend."""

from __future__ import annotations

import os
from datetime import datetime

from metaads import api
from metaads.formatting import _die, _output_json


def cmd_token_info(args) -> None:
    """Show current access token info (expiry, permissions)."""
    data = api._api_call("GET", "debug_token", {"input_token": api.META_ACCESS_TOKEN})
    token_data = data.get("data", {})

    if args.json:
        _output_json(token_data)
        return

    expires = token_data.get("expires_at", 0)
    if expires == 0:
        expiry_str = "Never (system user token)"
    else:
        expiry_dt = datetime.fromtimestamp(expires)
        days_left = (expiry_dt - datetime.now()).days
        expiry_str = f"{expiry_dt.strftime('%Y-%m-%d %H:%M')} ({days_left} days left)"

    print("Token Info:")
    print(f"  App:        {token_data.get('application', '---')}")
    print(f"  User ID:    {token_data.get('user_id', '---')}")
    print(f"  Type:       {token_data.get('type', '---')}")
    print(f"  Valid:      {token_data.get('is_valid', '---')}")
    print(f"  Expires:    {expiry_str}")
    scopes = token_data.get("scopes", [])
    if scopes:
        print(f"  Scopes:     {', '.join(scopes)}")


def _write_env_token(new_token: str) -> None:
    """Rewrite META_ACCESS_TOKEN in .env (backup to .env.bak first)."""
    env_path = os.path.join(api.BASE_DIR, ".env")
    if not os.path.isfile(env_path):
        _die("ERROR: .env not found — cannot write token.")
    with open(env_path) as f:
        lines = f.readlines()
    with open(env_path + ".bak", "w") as f:
        f.writelines(lines)
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("META_ACCESS_TOKEN="):
            lines[i] = f"META_ACCESS_TOKEN={new_token}\n"
            replaced = True
            break
    if not replaced:
        lines.append(f"META_ACCESS_TOKEN={new_token}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


def cmd_token_extend(args) -> None:
    """Exchange current token for a long-lived one (60 days)."""
    if not api.META_APP_ID or not api.META_APP_SECRET:
        _die("ERROR: META_APP_ID and META_APP_SECRET must be set in .env")

    data = api._api_call("GET", "oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": api.META_APP_ID,
        "client_secret": api.META_APP_SECRET,
        "fb_exchange_token": api.META_ACCESS_TOKEN,
    })

    new_token = data.get("access_token", "")
    expires_in = data.get("expires_in", 0)

    wrote_env = False
    if getattr(args, "write_env", False) and new_token:
        _write_env_token(new_token)
        wrote_env = True
        # Invalidate the cached expiry so the next run re-checks the new token
        try:
            os.remove(api._token_cache_file())
        except OSError:
            pass

    if args.json:
        out = dict(data)
        out["wrote_env"] = wrote_env
        _output_json(out)
        return

    days = expires_in // 86400
    print(f"New long-lived token generated ({days} days)." if expires_in
          else "New long-lived token generated (expiry not reported — check token-info).")
    print(f"  Token: {new_token[:20]}...{new_token[-10:]}")
    if wrote_env:
        print("  .env updated (previous version in .env.bak).")
    else:
        print("\n  Update .env manually with the new token, or re-run with --write-env:")
        print(f"  META_ACCESS_TOKEN={new_token}")

"""Output & formatting helpers. No API logic here."""

from __future__ import annotations

import json
import sys


def _err(msg: str) -> None:
    """Print a warning/notice to stderr."""
    print(msg, file=sys.stderr)


def _die(msg: str, code: int = 1) -> None:
    """Print an error to stderr and exit."""
    print(msg, file=sys.stderr)
    sys.exit(code)


def _output_json(data: object) -> None:
    """Print JSON to stdout."""
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _budget_to_cents(value: float) -> int:
    """Convert currency amount to cents (API format)."""
    return int(round(value * 100))


def _cents_to_budget(cents: int | str | None) -> float | None:
    """Convert cents to currency amount (display format)."""
    if cents is None:
        return None
    return int(cents) / 100.0


def _format_budget(cents: int | str | None, currency: str = "") -> str:
    """Format cents as currency string."""
    if cents is None:
        return "---"
    val = int(cents) / 100.0
    return f"{val:,.2f} {currency}".strip()


def _truncate(text: str | None, max_len: int = 40) -> str:
    """Truncate text for table display."""
    if not text:
        return "---"
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _fmt_delta(cur: float, prev: float, pct: bool = False) -> str:
    """Format a current-vs-previous delta as '12.3 (+15%)'."""
    if prev:
        change = (cur - prev) / prev * 100
        arrow = "+" if change >= 0 else ""
        suffix = f" ({arrow}{change:.0f}%)"
    elif cur:
        suffix = " (new)"
    else:
        suffix = ""
    val = f"{cur:.2f}" if pct or cur != int(cur) else f"{int(cur):,}"
    return f"{val}{suffix}"

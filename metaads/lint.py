"""Preflight lint: local checks of creative inputs before any API call.

Philosophy (mirrors Meta ad policy + placement truncation): clearly invalid
input dies, style/quality issues warn on stderr — the API's validate_only
stays the final arbiter.
"""

from __future__ import annotations

import os
import re
import struct

from metaads.formatting import _die, _err

# Recommended visible lengths before placement truncation (not hard API limits)
PRIMARY_TEXT_RECOMMENDED = 125
HEADLINE_RECOMMENDED = 40
DESCRIPTION_RECOMMENDED = 30

# Known CTA types (subset that covers common usage; the enum evolves, so an
# unknown value only warns — validate_only catches truly invalid ones)
KNOWN_CTAS = {
    "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "SUBSCRIBE", "BOOK_NOW", "CONTACT_US",
    "DOWNLOAD", "GET_OFFER", "GET_QUOTE", "APPLY_NOW", "ORDER_NOW", "WATCH_MORE",
    "PLAY_GAME", "LISTEN_NOW", "MESSAGE_PAGE", "CALL_NOW", "DONATE_NOW",
    "GET_DIRECTIONS", "SEND_MESSAGE", "WHATSAPP_MESSAGE", "GET_SHOWTIMES",
    "REQUEST_TIME", "SEE_MENU", "BUY_TICKETS", "USE_APP", "INSTALL_APP",
    "INSTALL_MOBILE_APP", "OPEN_LINK", "NO_BUTTON", "START_ORDER",
}

# Acronyms that are fine in ALL CAPS (Meta policy flags "unusual capitalization")
_CAPS_OK = {
    "AI", "CZ", "SK", "EU", "USA", "UK", "DPH", "IT", "HR", "B2B", "B2C",
    "CEO", "CTO", "PPC", "SEO", "ROI", "ROAS", "GA4", "GTM", "API", "CLI",
    "FAQ", "PDF", "VIP", "DIY", "CRM", "ERP", "HTML", "CSS", "SQL", "LED",
    "GPS", "USB", "TV", "PC", "OK", "NEW", "SALE",
}

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF"
    "\U0001F900-\U0001F9FF\U00002190-\U000021FF\U00002B00-\U00002BFF]"
)


def _style_findings(text: str, label: str) -> list[str]:
    findings: list[str] = []
    if text != text.strip():
        findings.append(f"{label}: leading/trailing whitespace")
    if "  " in text:
        findings.append(f"{label}: doubled spaces")
    if re.search(r"[!?]{2,}", text):
        findings.append(f"{label}: repeated punctuation (!!, ??) — Meta flags this")
    caps_words = [
        w for w in re.findall(r"[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]{4,}", text)
        if w not in _CAPS_OK
    ]
    if caps_words:
        findings.append(f"{label}: ALL-CAPS words {caps_words} — Meta policy flags shouting")
    emoji_count = len(_EMOJI_RE.findall(text))
    if emoji_count > 3:
        findings.append(f"{label}: {emoji_count} emoji — excessive symbols risk rejection")
    return findings


def lint_texts(message: str | None, headline: str | None, description: str | None) -> None:
    """Warn about texts likely to truncate or trip Meta editorial policy."""
    findings: list[str] = []
    for text, label, limit in (
        (message, "primary text", PRIMARY_TEXT_RECOMMENDED),
        (headline, "headline", HEADLINE_RECOMMENDED),
        (description, "description", DESCRIPTION_RECOMMENDED),
    ):
        if not text:
            continue
        if len(text) > limit:
            findings.append(f"{label}: {len(text)} chars (recommended ≤{limit}; will truncate in some placements)")
        findings.extend(_style_findings(text, label))
    for f in findings:
        _err(f"⚠ lint: {f}")


def lint_url(url: str | None) -> None:
    """Die on clearly invalid landing-page URLs."""
    if not url:
        return
    if not re.match(r"^https?://[^\s]+\.[^\s]+", url):
        _die(f"ERROR: lint: invalid URL '{url}' (must be http(s)://domain...)")
    if " " in url:
        _die(f"ERROR: lint: URL contains a space: '{url}'")


def lint_cta(cta: str | None) -> None:
    if cta and cta not in KNOWN_CTAS:
        _err(f"⚠ lint: CTA '{cta}' not in the known list — validate_only will verify it")


# ---------------------------------------------------------------------------
# Image dimension checks (no external deps — reads PNG/JPEG/GIF headers)
# ---------------------------------------------------------------------------

IMAGE_MIN_SIDE = 500       # below this we refuse (Meta feed minimum is 600×600)
IMAGE_RECOMMENDED_SIDE = 1080
_KNOWN_RATIOS = {"1:1": 1.0, "4:5": 0.8, "1.91:1": 1.91, "9:16": 0.5625, "16:9": 1.7778}


def _image_dimensions(path: str) -> tuple[int, int] | None:
    """Read width/height from PNG/JPEG/GIF headers. None = unknown format."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                w, h = struct.unpack(">II", head[16:24])
                return w, h
            if head.startswith(b"GIF8"):
                w, h = struct.unpack("<HH", head[6:10])
                return w, h
            if head.startswith(b"\xff\xd8"):
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w, h
                    (seg_len,) = struct.unpack(">H", f.read(2))
                    f.seek(seg_len - 2, 1)
    except (OSError, struct.error):
        return None
    return None


def lint_image(path: str) -> None:
    """Check a local image file before upload: dimensions and aspect ratio."""
    if not os.path.isfile(path):
        _die(f"ERROR: File not found: {path}")
    dims = _image_dimensions(path)
    if dims is None:
        _err(f"⚠ lint: cannot read dimensions of {os.path.basename(path)} (unknown format) — skipping checks")
        return
    w, h = dims
    if min(w, h) < IMAGE_MIN_SIDE:
        _die(
            f"ERROR: lint: image {os.path.basename(path)} is {w}×{h} px — below the "
            f"{IMAGE_MIN_SIDE}px minimum side (Meta feed needs ≥600×600, recommended ≥1080)."
        )
    if min(w, h) < IMAGE_RECOMMENDED_SIDE:
        _err(f"⚠ lint: image is {w}×{h} px — recommended ≥{IMAGE_RECOMMENDED_SIDE}px on the shorter side")
    ratio = w / h
    closest = min(_KNOWN_RATIOS.items(), key=lambda kv: abs(kv[1] - ratio))
    if abs(closest[1] - ratio) > 0.03:
        _err(
            f"⚠ lint: aspect ratio {ratio:.2f} ({w}×{h}) is not a standard placement ratio "
            f"(1:1, 4:5, 1.91:1, 9:16, 16:9) — Meta will crop"
        )

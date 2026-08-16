"""Synthetic multimodal and vision prompt-injection primitives for the Zodiac Bank lab.

Models the 2026 cross-modal injection surface: hidden text in image alt/caption
metadata, SVG <text> nodes, OCR'd screenshots, audio transcripts, and typographic
obfuscation (zero-width characters, homoglyphs, split markers). The module only
detects harmless, clearly-labeled markers; it never renders, OCRs, or executes a
payload.
"""

from __future__ import annotations

from typing import Any, Iterable

ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\ufeff"}
HOMOGLYPHS = str.maketrans({"о": "o", "е": "e", "а": "a", "с": "c", "р": "p", "х": "x", "і": "i"})
INSTRUCTION_TOKENS = ("ignore previous", "override", "run", "execute", "download", "reveal", "exfiltrate")


def extract_hidden_text(fields: dict[str, Any]) -> dict[str, Any]:
    """Extract hidden instruction-like content from synthetic multimodal fields."""
    markers: list[str] = []
    for key, value in fields.items():
        if not isinstance(value, str):
            continue
        lowered = value.casefold()
        for token in INSTRUCTION_TOKENS:
            if token in lowered:
                markers.append(token)
    return {"markers": sorted(set(markers)), "fields_checked": len(fields), "synthetic": True}


def detect_typographic_obfuscation(text: str) -> dict[str, Any]:
    """Detect zero-width characters and homoglyph substitutions in synthetic text."""
    if not isinstance(text, str):
        text = ""
    zero_width = sorted(set(char for char in text if char in ZERO_WIDTH))
    deobfuscated = text.translate(HOMOGLYPHS)
    changed = deobfuscated != text
    return {
        "zero_width_chars": zero_width,
        "homoglyph_substitution": changed,
        "obfuscated": bool(zero_width) or changed,
        "synthetic": True,
    }


def cross_modal_decision(record: dict[str, Any], *, trust_policy: str) -> dict[str, Any]:
    """Decide whether hidden content may cross from an image/audio channel to an action.

    ``trust_policy`` is ``"deny-untrusted"`` for the hardened posture: any hidden
    instruction-like marker found in an untrusted channel is quarantined.
    """
    fields = record.get("fields", {}) if isinstance(record, dict) else {}
    hidden = extract_hidden_text(fields)
    text = " ".join(str(value) for value in fields.values() if isinstance(value, str))
    obfuscation = detect_typographic_obfuscation(text)
    if trust_policy == "deny-untrusted" and (hidden["markers"] or obfuscation["obfuscated"]):
        return {"verdict": "block", "reason": "hidden or obfuscated multimodal instruction detected", "hidden": hidden, "obfuscation": obfuscation, "synthetic": True}
    return {"verdict": "allow", "reason": "no hidden multimodal instruction detected", "hidden": hidden, "obfuscation": obfuscation, "synthetic": True}


def snapshot() -> dict[str, Any]:
    return {"techniques": ["hidden-text", "typographic-obfuscation", "cross-modal"], "synthetic": True, "side_effects": False}

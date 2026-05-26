"""Tests for Channel A label formatting + UTF-8 truncation."""

from __future__ import annotations

import pytest

from client.datapackage import ClassifiedItem
from client.display import (
    MAX_MOON_LABEL_BYTES,
    format_moon_label,
    format_shop_moon_label,
    truncate_utf8,
)
from client.protocol import ItemKind


# --- truncate_utf8 -----------------------------------------------------


def test_truncate_passes_short_strings_through():
    assert truncate_utf8("hi", 30) == "hi"
    assert truncate_utf8("", 30) == ""


def test_truncate_appends_marker_on_clip():
    s = "a" * 50
    out = truncate_utf8(s, 10)
    assert out.endswith("-")
    assert len(out.encode("utf-8")) <= 10


def test_truncate_respects_byte_budget_with_multibyte_chars():
    # "é" is 2 bytes in UTF-8.
    s = "café" * 10  # 40 chars, ~50 bytes
    out = truncate_utf8(s, 10)
    assert len(out.encode("utf-8")) <= 10
    # And it didn't split a codepoint:
    assert out == out.encode("utf-8").decode("utf-8")


def test_truncate_never_splits_codepoint():
    # 4-byte emoji at the boundary.
    s = "ab" + "🦊" * 5  # 'a'+'b' = 2, fox = 4 bytes each → total 22
    out = truncate_utf8(s, 7)
    # Must not include half a fox.
    assert all(c in "ab🦊-" for c in out)


def test_truncate_zero_budget_drops_marker():
    # max_bytes < marker size: byte-trim path, no marker appended.
    out = truncate_utf8("hello world", 0)
    assert out == ""


def test_truncate_default_budget_matches_constant():
    assert MAX_MOON_LABEL_BYTES == 30


# --- format_moon_label -------------------------------------------------


def _moon(name: str, kingdom: str | None, shine_id: str) -> ClassifiedItem:
    return ClassifiedItem(ItemKind.MOON, name, kingdom=kingdom, shine_id=shine_id)


def test_format_outgoing_kingdomed_moon():
    # 2-char recipient keeps us under the 30-byte budget so we can assert the
    # full untruncated form. Real-world long names get clipped — see
    # test_format_long_kingdom_long_recipient_still_fits below.
    item = _moon("Cap Kingdom Power Moon", "Cap", "Power Moon")
    text = format_moon_label(item, recipient_slot="P3", me_slot="Mario")
    assert text == "Sent Cap Power Moon to P3"


def test_format_incoming_kingdomed_moon():
    item = _moon("Cap Kingdom Power Moon", "Cap", "Power Moon")
    text = format_moon_label(item, recipient_slot="Mario", me_slot="Mario")
    assert text == "Got Cap Power Moon!"


def test_format_generic_moon_no_kingdom():
    item = _moon("Power Moon", None, "Power Moon")
    text = format_moon_label(item, recipient_slot="Mario", me_slot="Mario")
    assert text == "Got Power Moon!"


def test_format_multi_moon_carries_kingdom():
    item = _moon("Sand Kingdom Multi-Moon", "Sand", "Multi-Moon")
    text = format_moon_label(item, recipient_slot="Mario", me_slot="Mario")
    assert text == "Got Sand Multi-Moon!"


def test_format_capture_routed_to_other():
    item = ClassifiedItem(ItemKind.CAPTURE, "Goomba", cap="Goomba")
    text = format_moon_label(item, recipient_slot="Slot2", me_slot="Mario")
    assert text == "Sent Goomba to Slot2"


def test_format_long_kingdom_long_recipient_still_fits():
    item = _moon("Darker Side Kingdom Power Moon", "Darker Side", "Power Moon")
    text = format_moon_label(item, recipient_slot="VeryLongPlayerNameHere", me_slot="me")
    assert len(text.encode("utf-8")) <= MAX_MOON_LABEL_BYTES
    assert text.endswith("-")  # got clipped


def test_format_other_kind_uses_raw_name():
    item = ClassifiedItem(ItemKind.OTHER, "Sphynx's Treasure Vault")
    text = format_moon_label(item, recipient_slot="me", me_slot="me")
    assert text == "Got Sphynx's Treasure Vault!"


def test_format_recipient_with_unicode():
    item = _moon("Cap Kingdom Power Moon", "Cap", "Power Moon")
    text = format_moon_label(item, recipient_slot="Pléyer", me_slot="me")
    # No split-codepoint corruption even if the truncation hits the é.
    assert len(text.encode("utf-8")) <= MAX_MOON_LABEL_BYTES
    decoded = text.encode("utf-8").decode("utf-8")
    assert decoded == text


def test_format_me_slot_none_treats_as_outgoing():
    item = _moon("Cap Kingdom Power Moon", "Cap", "Power Moon")
    # No own-slot known yet (early connect) — anything reads as outgoing.
    text = format_moon_label(item, recipient_slot="anyone", me_slot=None)
    assert text.startswith("Sent ")


# --- format_shop_moon_label --------------------------------------------
# The shop slot is read BEFORE purchase, so the tense drops the past-tense
# "Got" / "Sent" framing from format_moon_label.


def test_format_shop_self_strips_got():
    item = _moon("Cap Kingdom Power Moon", "Cap", "Power Moon")
    text = format_shop_moon_label(item, recipient_slot="Mario", me_slot="Mario")
    assert text == "Cap Power Moon"


def test_format_shop_other_uses_for():
    item = _moon("Cap Kingdom Power Moon", "Cap", "Power Moon")
    text = format_shop_moon_label(item, recipient_slot="P3", me_slot="Mario")
    assert text == "Cap Power Moon for P3"


def test_format_shop_capture_self():
    # Hypothetical: a capture in a shop slot. Same body-shortening as
    # format_moon_label, no tense decoration when routed to me.
    item = ClassifiedItem(ItemKind.CAPTURE, "Goomba", cap="Goomba")
    text = format_shop_moon_label(item, recipient_slot="me", me_slot="me")
    assert text == "Goomba"


def test_format_shop_truncates_when_too_long():
    item = _moon("Darker Side Kingdom Power Moon", "Darker Side", "Power Moon")
    text = format_shop_moon_label(item, recipient_slot="VeryLongPlayerNameHere", me_slot="me")
    assert len(text.encode("utf-8")) <= MAX_MOON_LABEL_BYTES
    assert text.endswith("-")  # got clipped


def test_format_shop_me_slot_none_treats_as_outgoing():
    item = _moon("Cap Kingdom Power Moon", "Cap", "Power Moon")
    text = format_shop_moon_label(item, recipient_slot="anyone", me_slot=None)
    # Pre-auth: no past-tense "Sent", but "for <slot>" still applies.
    assert text == "Cap Power Moon for anyone"

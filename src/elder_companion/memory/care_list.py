"""Care list (spec 2.5): the people and topics on her mind lately, for the family."""

from __future__ import annotations

from collections.abc import Iterable

from elder_companion.models import MemoryItem

CARE_KINDS = ("person", "topic")


def care_list(items: Iterable[MemoryItem], min_mentions: int) -> list[MemoryItem]:
    """Visible people and topics mentioned at least `min_mentions` times, most recent first.
    Pure: pass it `privacy.visible_memory(...)`; private items are dropped here too."""
    kept = [
        i
        for i in items
        if i.kind in CARE_KINDS and not i.private and i.mention_count >= min_mentions
    ]
    return sorted(kept, key=lambda i: (i.last_seen, i.id), reverse=True)

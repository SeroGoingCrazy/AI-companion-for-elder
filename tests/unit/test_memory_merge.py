"""H2: memory merge rules, visibility matching and the extractor's quote check."""

from datetime import date, datetime, timedelta

import pytest

from elder_companion.llm.mock import MockLLMClient
from elder_companion.memory.extractor import MemoryExtractor, Turn
from elder_companion.memory.schema import MemoryItemIn, memory_schema
from elder_companion.memory.store import find_match, merge_memory_item
from elder_companion.models import MemoryItem

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, 16, 0)
TODAY = date(2026, 9, 29)


def _item(**kw) -> MemoryItemIn:
    base = {
        "kind": "follow_up",
        "subject": "orchid",
        "text": "planning to repot her orchid",
        "due_in_days": 3,
        "raw_quote": "repot my orchid",
    }
    return MemoryItemIn(**{**base, **kw})


def _row(**kw) -> MemoryItem:
    base = {
        "id": 1,
        "elder_id": 1,
        "kind": "follow_up",
        "subject": "Orchid",
        "text": "old",
        "raw_quote": "old",
        "private": False,
        "mention_count": 1,
        "due_date": TODAY,
        "status": "open",
        "first_seen": NOW - timedelta(days=2),
        "last_seen": NOW - timedelta(days=2),
    }
    return MemoryItem(**{**base, **kw})


def test_new_follow_up_gets_a_due_date() -> None:
    r = merge_memory_item(None, _item(), NOW, today=TODAY, message_id=5, private=False)
    assert r.is_new and r.values["due_date"] == TODAY + timedelta(days=3)
    assert r.values["status"] == "open" and r.values["mention_count"] == 1
    no_date = merge_memory_item(
        None,
        _item(due_in_days=None),
        NOW,
        today=TODAY,
        message_id=5,
        private=False,
        default_delay_days=1,
    )
    assert no_date.values["due_date"] == TODAY + timedelta(days=1)


def test_non_follow_ups_have_no_due_date() -> None:
    r = merge_memory_item(
        None,
        _item(kind="person", subject="Rosa", due_in_days=None),
        NOW,
        today=TODAY,
        message_id=5,
        private=False,
    )
    assert r.values["due_date"] is None


def test_repeat_mention_bumps_count_and_updates_words() -> None:
    r = merge_memory_item(
        _row(kind="person", mention_count=2),
        _item(kind="person"),
        NOW,
        today=TODAY,
        message_id=9,
        private=False,
    )
    assert not r.is_new
    assert r.values["mention_count"] == 3 and r.values["last_seen"] == NOW
    assert r.values["raw_quote"] == "repot my orchid" and r.values["message_id"] == 9


def test_asked_follow_up_reopens_only_with_an_explicit_new_date() -> None:
    asked = _row(status="asked")
    talk = merge_memory_item(
        asked, _item(due_in_days=None), NOW, today=TODAY, message_id=9, private=False
    )
    assert "status" not in talk.values and "due_date" not in talk.values
    plan = merge_memory_item(
        asked, _item(due_in_days=7), NOW, today=TODAY, message_id=9, private=False
    )
    assert plan.values["status"] == "open"
    assert plan.values["due_date"] == TODAY + timedelta(days=7)


def test_item_is_private_only_while_every_mention_was() -> None:
    r = merge_memory_item(
        _row(private=True), _item(), NOW, today=TODAY, message_id=9, private=False
    )
    assert r.values["private"] is False


def test_private_mention_never_merges_into_a_visible_item() -> None:
    visible = _row(id=1, kind="person", subject="Linda")
    item = _item(kind="person", subject="linda")
    assert find_match([visible], item, private=True) is None
    assert find_match([visible], item, private=False) is visible
    hidden = _row(id=2, kind="person", subject="Linda", private=True)
    assert find_match([visible, hidden], item, private=True) is hidden
    assert find_match([hidden], item, private=False) is hidden  # becomes visible


def test_stories_merge_only_on_the_same_words() -> None:
    story = _row(kind="story", subject="teaching", raw_quote="my first class")
    assert (
        find_match(
            [story], _item(kind="story", subject="teaching", raw_quote="another"), private=False
        )
        is None
    )
    assert (
        find_match(
            [story], _item(kind="story", subject="x", raw_quote="My first class"), private=False
        )
        is story
    )


def test_schema_is_strict() -> None:
    schema = memory_schema()
    item = schema["$defs"]["MemoryItemIn"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


class ScriptedLLM(MockLLMClient):
    def __init__(self, result: dict) -> None:
        super().__init__({})
        self.result = result

    def extract_json(self, messages, *, schema, name):  # noqa: ANN001
        self.calls.append(("extract_json", messages))
        return self.result


def test_extractor_drops_invented_quotes_and_duplicates() -> None:
    llm = ScriptedLLM(
        {
            "items": [
                {
                    "kind": "person",
                    "subject": "Rosa",
                    "text": "t",
                    "due_in_days": None,
                    "raw_quote": "Rosa brought soup",
                },
                {
                    "kind": "person",
                    "subject": "rosa",
                    "text": "t",
                    "due_in_days": None,
                    "raw_quote": "Rosa",
                },
                {
                    "kind": "topic",
                    "subject": "garden",
                    "text": "t",
                    "due_in_days": None,
                    "raw_quote": "the garden",
                },
            ],
            "privacy_request": {"requested": False, "covers_message_ids": []},
        }
    )
    out = MemoryExtractor(llm).extract(7, "Rosa brought soup!", [Turn(6, "assistant", "Hi")])
    assert [(i.kind, i.subject) for i in out.items] == [("person", "Rosa")]
    prompt = llm.calls[0][1][1]["content"]
    assert "[#6] Companion: Hi" in prompt and prompt.endswith("[#7]:\nRosa brought soup!")

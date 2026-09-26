import pytest

from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.symptoms.extractor import SymptomExtractor, verify_quote
from elder_companion.symptoms.schema import SCHEMA_NAME, SymptomItem

pytestmark = pytest.mark.unit


def _item(**kw) -> dict:
    base = {
        "canonical": "dizziness",
        "label": "dizziness",
        "body_part": None,
        "severity": "unknown",
        "duration": None,
        "onset": None,
        "status": "new",
        "raw_quote": "dizzy",
    }
    return {**base, **kw}


def _llm(*items: dict) -> MockLLMClient:
    return MockLLMClient({"json": {SCHEMA_NAME: {"default": {"symptoms": list(items)}}}})


@pytest.mark.parametrize(
    ("quote", "text", "ok"),
    [
        ("dizzy", "I felt a bit dizzy today", True),
        ("I'm DIZZY", "i’m dizzy, honestly", True),  # case + curly apostrophe
        ("头有点晕", "这两天早上起来，头有点晕。", True),
        ("胸口闷 喘不上气", "胸口闷，喘不上气", True),  # punctuation/space differences
        ("my knee hurts", "I felt a bit dizzy today", False),
        ("膝盖", "我头晕", False),
        ("...", "anything", False),  # nothing left after normalization
    ],
)
def test_verify_quote(quote: str, text: str, ok: bool) -> None:
    assert verify_quote(SymptomItem(**_item(raw_quote=quote)), text) is ok


def test_extracts_with_mock_config() -> None:
    extractor = SymptomExtractor(MockLLMClient.from_config())
    assert [i.canonical for i in extractor.extract("这两天早上起来头有点晕")] == ["dizziness"]
    chest = extractor.extract("胸口闷，喘不上气")
    assert [i.canonical for i in chest] == ["chest_pain", "shortness_of_breath"]
    assert extractor.extract("I watered the roses today.") == []


def test_hallucinated_quote_is_dropped() -> None:
    llm = _llm(_item(), _item(canonical="joint_pain", raw_quote="my knee hurts"))
    items = SymptomExtractor(llm).extract("I felt a bit dizzy today")
    assert [i.canonical for i in items] == ["dizziness"]


def test_duplicates_collapse_but_other_keeps_distinct_labels() -> None:
    llm = _llm(
        _item(),
        _item(raw_quote="dizzy today"),
        _item(canonical="other", label="rash", raw_quote="rash"),
        _item(canonical="other", label="Rash ", raw_quote="rash"),
        _item(canonical="other", label="itchy eyes", raw_quote="itchy eyes"),
    )
    items = SymptomExtractor(llm).extract("dizzy today, a rash and itchy eyes")
    assert [(i.canonical, i.label) for i in items] == [
        ("dizziness", "dizziness"),
        ("other", "rash"),
        ("other", "itchy eyes"),
    ]


def test_schema_violation_raises_llm_error() -> None:
    llm = _llm(_item(canonical="heartbreak"))
    with pytest.raises(LLMError, match="schema"):
        SymptomExtractor(llm).extract("I felt a bit dizzy")


def test_request_has_strict_schema_enum_and_context() -> None:
    llm = _llm()
    turns = [
        {"role": "assistant", "content": "How is your knee today?"},
    ]
    SymptomExtractor(llm).extract("Much better, thanks", turns)
    _, call = llm.calls[-1]
    assert call["name"] == SCHEMA_NAME
    system, user = call["messages"]
    assert "- chest_pain:" in system["content"] and "- other:" in system["content"]
    assert "$" not in system["content"]  # every placeholder was filled
    assert user["content"].index("Companion: How is your knee today?") < user["content"].index(
        "Much better, thanks"
    )
    assert user["content"].endswith("Her current utterance:\nMuch better, thanks")


def test_no_context_is_marked() -> None:
    llm = _llm()
    SymptomExtractor(llm).extract("hello")
    assert "(none)" in llm.calls[-1][1]["messages"][1]["content"]


def test_mock_ignores_symptoms_in_context() -> None:
    # "chest" in an earlier turn must not shadow the dizziness rule for the current utterance
    extractor = SymptomExtractor(MockLLMClient.from_config())
    turns = [{"role": "user", "content": "my chest felt odd yesterday"}]
    assert [i.canonical for i in extractor.extract("I'm dizzy", turns)] == ["dizziness"]

from typing import Any

import pytest

from elder_companion.db import init_db, make_engine, make_sessionmaker
from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Elder, Message
from elder_companion.redaction import Redactor, family_text, redact_messages, redact_rules


@pytest.mark.parametrize(
    ("text", "hidden", "placeholder"),
    [
        ("Call me at (555) 123-4567 tonight", "123-4567", "[phone number]"),
        ("my number is 555.123.4567", "4567", "[phone number]"),
        ("Amy's cell is +1 415 555 0199", "0199", "[phone number]"),
        ("我的手机是138 1234 5678", "5678", "[phone number]"),
        ("女儿电话是555-123-4567", "4567", "[phone number]"),
        ("email me at maggie.lee@example.com", "maggie.lee", "[email]"),
        ("my social is 123-45-6789", "6789", "[ID number]"),
        ("身份证号110101194003071234", "1234", "[ID number]"),
        ("the card is 4111 1111 1111 1111", "1111", "[card number]"),
        ("Medicare number 1EG4TE5MK73 and account 00012345678", "12345678", "[number]"),
        ("my password is Rosebud42, don't forget", "Rosebud42", "[password]"),
        ("the PIN is 4821", "4821", "[password]"),
        ("银行卡密码是860214", "860214", "[密码]"),
        ("I live at 42 Maple Street, Apt 3B now", "Maple", "[address]"),
        ("我住在长安路88号", "长安路", "[地址]"),
    ],
)
def test_rules_hide_structured_details(text: str, hidden: str, placeholder: str) -> None:
    out = redact_rules(text)
    assert hidden not in out
    assert placeholder in out


def test_chinese_address_keeps_the_words_around_it() -> None:
    assert redact_rules("我住在长安路88号。") == "我住在[地址]。"


@pytest.mark.parametrize(
    "text",
    [
        "My knee hurts a little today, about a 3 out of 10.",
        "Blood pressure was 140/90 at 9:30 this morning.",
        "I took 2 pills on Sep 24, 2026.",
        "Amy called and we talked about the garden for 20 minutes.",
        "我今天头有点晕，吃了两片药。",
        "I got a new hair pin today.",
    ],
)
def test_rules_keep_ordinary_talk(text: str) -> None:
    assert redact_rules(text) == text


class FakeLLM(MockLLMClient):
    def __init__(self, result: Any) -> None:
        super().__init__({})
        self.result = result

    def extract_json(self, messages, *, schema, name):  # noqa: ANN001
        self.calls.append(("extract_json", {"name": name, "messages": messages}))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _msg(id: int, text: str, role: str = "user") -> Message:
    return Message(id=id, elder_id=1, role=role, text=text)


def test_redactor_keeps_known_ids_and_reapplies_rules() -> None:
    llm = FakeLLM(
        {
            "messages": [
                {"id": 1, "text": "My neighbour Rosa's number is 555-123-4567"},
                {"id": 99, "text": "invented"},
            ]
        }
    )
    out = Redactor(llm).redact([_msg(1, "x"), _msg(2, "y", "assistant")])
    assert out == {1: "My neighbour Rosa's number is [phone number]"}
    prompt = llm.calls[0][1]["messages"][1]["content"]
    assert "[#1] Elder: x" in prompt and "[#2] Companion: y" in prompt


def test_redactor_schema_mismatch_is_llm_error() -> None:
    with pytest.raises(LLMError):
        Redactor(FakeLLM({"nope": []})).redact([_msg(1, "x")])


def test_family_text_falls_back_to_rules_until_redacted() -> None:
    m = _msg(1, "call 555-123-4567")
    assert family_text(m) == "call [phone number]"
    m.family_text = "call [phone number] (Rosa)"
    assert family_text(m) == "call [phone number] (Rosa)"


@pytest.fixture
def session_factory(tmp_path):  # noqa: ANN001, ANN201
    engine = make_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    init_db(engine)
    factory = make_sessionmaker(engine)
    with factory() as s:
        s.add(Elder(id=1, name="Margaret", nickname="Maggie"))
        s.add_all([_msg(1, "PIN 4821, and Rosa Diaz at 12 Oak Lane has cancer"), _msg(2, "ok")])
        s.commit()
    return factory


def test_redact_messages_stores_llm_output(session_factory) -> None:  # noqa: ANN001
    llm = FakeLLM({"messages": [{"id": 1, "text": "PIN 4821, and Rosa has cancer"}]})
    redact_messages(session_factory, Redactor(llm), [1, 2, None])
    with session_factory() as s:
        assert s.get(Message, 1).family_text == "PIN [password], and Rosa has cancer"
        assert s.get(Message, 2).family_text == "ok"  # left out by the model: rules only


def test_redact_messages_survives_llm_failure(session_factory) -> None:  # noqa: ANN001
    redact_messages(session_factory, Redactor(FakeLLM(LLMError("down"))), [1])
    with session_factory() as s:
        stored = s.get(Message, 1).family_text
    assert "4821" not in stored and "Oak Lane" not in stored

import re

import pytest

from elder_companion.chat.postprocess import guard_medical_advice, limit_questions, tidy_reply

pytestmark = pytest.mark.unit

CJK = re.compile(r"[一-鿿]")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # untouched: zero or one question
        ("Please call 911 right now.", "Please call 911 right now."),
        ("That sounds hard. Did you sleep at all?", "That sounds hard. Did you sleep at all?"),
        ("Is it better? I hope so.", "Is it better? I hope so."),
        # two questions -> keep up to the first one
        ("现在疼得厉害吗？还是有点酸痛？", "现在疼得厉害吗？"),
        (
            "头晕让人很不舒服，你早上头晕是一直都有，还是偶尔才这样？有没有别的感觉？",
            "头晕让人很不舒服，你早上头晕是一直都有，还是偶尔才这样？",
        ),
        ("Oh no. Is it your knee? Or your back? Tell me.", "Oh no. Is it your knee?"),
        ("Mixed marks? 还有别的吗？", "Mixed marks?"),
    ],
)
def test_limit_questions(text: str, expected: str) -> None:
    assert limit_questions(text) == expected


def test_limit_questions_custom_max() -> None:
    assert limit_questions("A? B? C?", max_questions=2) == "A? B?"


def test_tidy_reply_strips_whitespace() -> None:
    assert tidy_reply("  Hello there!  \n") == "Hello there!"


@pytest.mark.parametrize(
    "reply",
    [
        "Try taking some ibuprofen for the knee.",
        "You could put some ice on it. Does it still hurt?",
        "Take 200 mg twice a day and you'll feel better.",
        "It sounds like arthritis, which is common.",
        "Don't worry, it's nothing serious.",
        "建议你吃点止痛药。",
        "可能是关节炎，热敷一下会好点。",
        "睡前多喝热水就好了。",
    ],
)
def test_medical_advice_is_replaced(reply: str) -> None:
    out = guard_medical_advice(reply)
    assert out != reply
    assert ("医疗建议" in out) if CJK.search(reply) else ("can't give medical advice" in out)


@pytest.mark.parametrize(
    "reply",
    [
        "Oh, that doesn't sound pleasant at all. Does it happen right when you get out of bed?",
        "Did you take your blood pressure pill this morning?",
        "Maggie, that could be serious. Please call 911 right now, and then call Amy.",
        "That walk sounds lovely. It's probably the best part of your day.",
        "膝盖又疼了，真让人心疼。要不要我跟Amy说一声？",
        "I can't give medical advice, but your doctor is the right person to ask.",
    ],
)
def test_ordinary_replies_pass(reply: str) -> None:
    assert guard_medical_advice(reply) == reply

import pytest

from elder_companion.chat.postprocess import limit_questions, tidy_reply

pytestmark = pytest.mark.unit


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

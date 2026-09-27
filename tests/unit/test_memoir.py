"""H10: memoir — visible stories only, retellings cached after the first load."""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import MemoryItem, Message
from elder_companion.seed import seed_history
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.unit


class CountingLLM(MockLLMClient):
    down = False

    def chat(self, messages, *, max_tokens=None):  # noqa: ANN001
        if self.down:
            raise LLMError("down")
        return super().chat(messages, max_tokens=max_tokens)

    @property
    def retellings(self) -> int:
        return sum(
            1
            for name, args in self.calls
            if name == "chat" and "keepsake memoir" in args[0]["content"]
        )


@pytest.fixture
def llm() -> CountingLLM:
    return CountingLLM.from_config()


@pytest.fixture
def client(settings: Settings, llm: CountingLLM) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=llm)) as c:
        with c.app.state.sessionmaker() as s:
            seed_history(s, datetime.now(settings.chat.tz))
            # a private story must never show up
            msg = s.scalars(select(Message).where(Message.private.is_(True))).first()
            s.add(
                MemoryItem(
                    elder_id=1,
                    kind="story",
                    subject="secret",
                    text="t",
                    raw_quote="SECRET STORY",
                    message_id=msg.id,
                    private=True,
                )
            )
            s.commit()
        yield c


def test_stories_appear_oldest_first_with_excerpts(client: TestClient) -> None:
    page = client.get("/family/memoir").text
    first = page.index("When I started teaching in 1972")
    second = page.index("he sketched our little house in Suzhou")
    assert first < second
    assert "SECRET STORY" not in page
    assert "glad my family will keep it" in page  # the (mock) retelling


def test_second_load_makes_no_llm_calls(client: TestClient, llm: CountingLLM) -> None:
    client.get("/family/memoir")
    assert llm.retellings == 2
    client.get("/family/memoir")
    assert llm.retellings == 2


def test_model_down_shows_her_words_and_retries_later(client: TestClient, llm: CountingLLM) -> None:
    llm.down = True
    page = client.get("/family/memoir").text
    assert "When I started teaching in 1972" in page and "memoir-retelling" not in page
    llm.down = False
    assert "memoir-retelling" in client.get("/family/memoir").text

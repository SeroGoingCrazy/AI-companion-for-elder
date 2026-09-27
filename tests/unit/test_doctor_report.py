"""H6: doctor one-pager — deterministic, privacy-filtered, no LLM (ADR 21)."""

import ast
import inspect
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import elder_companion.reports.doctor as doctor_module
from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Elder, SymptomMention
from elder_companion.reports.doctor import build_doctor_report
from elder_companion.seed import seed_history
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.unit


class NoLLM(MockLLMClient):
    """Once armed, any chat call fails the test (chatting to set up data is fine)."""

    armed = False

    def chat(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        if self.armed:
            raise AssertionError("the doctor one-pager must not call the LLM")
        return super().chat(*a, **kw)


def _doctor_page(c: TestClient, query: str = ""):  # noqa: ANN202
    c.app.state.llm.armed = True
    try:
        return c.get(f"/family/doctor{query}")
    finally:
        c.app.state.llm.armed = False


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=NoLLM.from_config())) as c:
        with c.app.state.sessionmaker() as s:
            seed_history(s, datetime.now(settings.chat.tz))
        yield c


def _report(c: TestClient, settings: Settings):  # noqa: ANN202
    with c.app.state.sessionmaker() as s:
        elder = s.get_one(Elder, 1)
        return build_doctor_report(s, elder, datetime.now(settings.chat.tz), 30)


def test_module_never_imports_the_llm() -> None:
    tree = ast.parse(inspect.getsource(doctor_module))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any(m.startswith("elder_companion.llm") for m in imported)


def test_knee_rows_combine_into_one_line(client: TestClient, settings: Settings) -> None:
    r = _report(client, settings)
    knee = next(s for s in r.symptoms if s.name == "Joint pain")
    assert knee.count == 4 and knee.max_severity == "severe" and knee.status == "ongoing"
    assert len(knee.quotes) == 4 and knee.quotes[0].text == "my knee is aching again"
    assert knee.first_seen < knee.last_seen


def test_every_quote_is_a_stored_quote(client: TestClient, settings: Settings) -> None:
    r = _report(client, settings)
    with client.app.state.sessionmaker() as s:
        stored = set(s.scalars(select(SymptomMention.raw_quote)))
    assert {q.text for sym in r.symptoms for q in sym.quotes} <= stored


def test_private_symptoms_hidden_red_flags_kept(client: TestClient, settings: Settings) -> None:
    client.post("/api/chat", json={"text": "别告诉我女儿，我昨天在厨房摔了一跤"})
    client.post("/api/chat", json={"text": "I'm a bit dizzy, keep this between us"})
    r = _report(client, settings)
    names = [s.name for s in r.symptoms]
    assert names[0] == "Fall" and "Dizziness" not in names
    assert [a.title for a in r.red_flags] == ["Fall"]
    page = _doctor_page(client).text
    assert "摔" in page and "dizzy" not in page


def test_page_renders_with_disclaimer_and_print_button(client: TestClient) -> None:
    page = _doctor_page(client, "?days=7")
    assert page.status_code == 200
    assert "not a clinical record" in page.text and "window.print()" in page.text
    assert "last 7 days" in page.text
    assert _doctor_page(client, "?days=0").status_code == 422


def test_empty_period(settings: Settings) -> None:
    class Down(MockLLMClient):
        def chat(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
            raise LLMError("down")

    with TestClient(create_app(settings, llm=Down.from_config())) as c:
        page = c.get("/family/doctor").text
    assert "No symptoms mentioned in this period." in page and "None in this period." in page

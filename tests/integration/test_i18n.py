"""Both surfaces render in English and Simplified Chinese, and the switch is reachable."""

import pytest
import yaml
from fastapi.testclient import TestClient

from elder_companion.i18n import I18N_DIR, LANG_COOKIE, available_langs, match, resolve, strings

pytestmark = pytest.mark.integration

PAGES = ["/elder", "/family"]


def _flat(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        out |= _flat(v, f"{prefix}{k}.") if isinstance(v, dict) else {f"{prefix}{k}"}
    return out


def test_every_language_defines_every_key() -> None:
    """A missing key would fall back to English silently; catch it here instead."""
    en = _flat(yaml.safe_load((I18N_DIR / "en.yaml").read_text(encoding="utf-8")))
    for lang in available_langs():
        other = _flat(yaml.safe_load((I18N_DIR / f"{lang}.yaml").read_text(encoding="utf-8")))
        assert other == en, f"{lang}.yaml differs: missing {en - other}, extra {other - en}"


def test_placeholders_match_across_languages() -> None:
    """A translated string that drops {nickname} renders a sentence with a hole in it."""
    import re

    def holes(d: dict, prefix: str = "") -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out |= holes(v, f"{prefix}{k}.")
            elif isinstance(v, str):
                out[f"{prefix}{k}"] = set(re.findall(r"\{(\w+)\}", v))
        return out

    en = holes(strings("en"))
    for lang in available_langs():
        for key, expected in holes(strings(lang)).items():
            assert expected == en[key], f"{lang}: {key} has {expected}, English has {en[key]}"


@pytest.mark.parametrize("path", PAGES)
def test_query_param_selects_the_language(client: TestClient, path: str) -> None:
    assert 'lang="zh"' in client.get(f"{path}?lang=zh").text
    assert 'lang="en"' in client.get(f"{path}?lang=en").text


@pytest.mark.parametrize("path", PAGES)
def test_cookie_is_remembered(client: TestClient, path: str) -> None:
    assert 'lang="zh"' in client.get(path, cookies={LANG_COOKIE: "zh"}).text


@pytest.mark.parametrize("path", PAGES)
def test_query_param_beats_the_cookie(client: TestClient, path: str) -> None:
    r = client.get(f"{path}?lang=en", cookies={LANG_COOKIE: "zh"})
    assert 'lang="en"' in r.text


@pytest.mark.parametrize("path", PAGES)
def test_default_is_english(client: TestClient, path: str) -> None:
    assert 'lang="en"' in client.get(path).text


@pytest.mark.parametrize("path", PAGES)
def test_switch_offers_the_other_language(client: TestClient, path: str) -> None:
    assert 'data-next="zh"' in client.get(f"{path}?lang=en").text
    assert 'data-next="en"' in client.get(f"{path}?lang=zh").text


def test_chinese_page_has_no_leftover_english_chrome(client: TestClient) -> None:
    """The strings the app owns are translated; model output and the elder's name are not."""
    html = client.get("/family?lang=zh").text
    for leftover in ("Refresh", "Alerts", "Fall detection", "Conversation", "Loading…"):
        assert leftover not in html, leftover


def test_alert_titles_follow_the_reader(client: TestClient) -> None:
    """Stored titles are English; the reader sees their own language."""
    client.post(
        "/api/alerts",
        json={"type": "symptom", "level": "high", "title": "Chest pain", "content": "x"},
    )
    assert client.get("/api/alerts?limit=1").json()[0]["title"] == "Chest pain"


def test_unknown_language_falls_back_to_english(client: TestClient) -> None:
    assert 'lang="en"' in client.get("/elder?lang=klingon").text


def test_match_distinguishes_english_from_unknown() -> None:
    assert match("en") == "en"
    assert match("zh-Hans-CN") == "zh"
    assert match("klingon") is None
    assert match(None) is None


def test_accept_language_is_the_last_resort() -> None:
    assert resolve(None, None, "zh-CN,zh;q=0.9,en;q=0.8") == "zh"
    assert resolve(None, "en", "zh-CN") == "en", "a remembered choice outranks the browser"

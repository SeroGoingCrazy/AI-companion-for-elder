"""Replay the demo script against a running server and capture screenshots.

For the pitch deck and the README: every shot is the real app, not a mockup. The elder
app is captured at iPhone 15 Pro size because that is how it is demoed; the dashboard at
laptop size because that is where the family watches it.

    uv run elder-web                                   # terminal 1 (LLM_PROVIDER=mock)
    uv run --with playwright python scripts/demo_shots.py

Chrome is driven through the copy already installed on the machine, so there is no
browser download. Output lands in demo/shots/.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / "demo" / "shots"
ELDER_ID = 1

PHONE = {"width": 430, "height": 932}  # iPhone 15 Pro
LAPTOP = {"width": 1440, "height": 900}

# The DEV_SPEC demo script: a warm opening, a mild symptom, then the red flag.
MILD = "I felt a bit dizzy this morning when I got up"
RED_FLAG = "my chest feels tight and I can't catch my breath"


def say(text: str) -> str:
    """One chat turn, as the elder app's text fallback would send it."""
    r = httpx.post(f"{BASE}/api/chat", json={"elder_id": ELDER_ID, "text": text}, timeout=30)
    r.raise_for_status()
    return r.json()["reply_text"]


def shoot(page: Page, name: str) -> None:
    path = OUT / f"{name}.png"
    page.screenshot(path=path)
    print(f"  {path.relative_to(OUT.parent.parent)}")


def main() -> int:
    try:
        httpx.get(f"{BASE}/healthz", timeout=3).raise_for_status()
    except Exception:
        print(f"No server at {BASE} — start it with `uv run elder-web` first.", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    print("capturing:")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")

        # --- elder app, on a phone ---------------------------------------------------
        phone = browser.new_context(
            viewport=PHONE,
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            # The hold-to-talk button asks for the mic on open; grant it so the shot shows
            # the app's real state rather than a permission prompt.
            permissions=["microphone"],
        )
        page = phone.new_page()

        page.goto(f"{BASE}/elder", wait_until="networkidle")
        shoot(page, "1-elder-start")

        page.click("#start-btn")
        page.wait_for_selector("#chat-screen:not([hidden])")
        page.wait_for_selector(".conversation :text('Good morning')", timeout=15_000)
        time.sleep(1.0)  # let the sun's rays settle into the speaking state
        shoot(page, "2-elder-greeting")

        # Drive the page itself rather than the API, so the shot shows the real
        # conversation: the elder's own bubble above Sunny's reply. Reloading would
        # not do — the app opens a fresh screen every time.
        page.click("summary:has-text('Type instead')")
        page.fill("#text-input", MILD)
        page.press("#text-input", "Enter")
        page.wait_for_selector(f".conversation :text('{MILD[:20]}')", timeout=20_000)
        page.wait_for_selector(".conversation :text('dizzy')", timeout=20_000)
        time.sleep(1.5)
        shoot(page, "3-elder-conversation")
        phone.close()

        # --- family dashboard, on a laptop -------------------------------------------
        desk = browser.new_context(viewport=LAPTOP, device_scale_factor=2)
        page = desk.new_page()

        page.goto(f"{BASE}/family", wait_until="networkidle")
        time.sleep(2.0)  # the daily summary is generated on open
        shoot(page, "4-family-dashboard")

        # The moment the demo is built around: a red-flag symptom, pushed over SSE while
        # the dashboard is already open and watching.
        say(RED_FLAG)
        page.wait_for_selector(".alert-banner, [class*='banner']", timeout=20_000)
        time.sleep(1.5)
        shoot(page, "5-family-urgent-alert")

        page.reload(wait_until="networkidle")
        time.sleep(2.5)
        page.screenshot(path=OUT / "6-family-full.png", full_page=True)
        print("  demo/shots/6-family-full.png")

        desk.close()
        browser.close()

    print(f"\n{len(list(OUT.glob('*.png')))} shots in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

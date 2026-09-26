"""Smoke check: uv run python -m elder_companion.llm --ping  (uses the configured provider)."""

from __future__ import annotations

import argparse

from elder_companion.llm.client import get_llm
from elder_companion.settings import get_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="LLM client smoke check")
    parser.add_argument("--ping", action="store_true", help="send one chat request")
    args = parser.parse_args(argv)
    if not args.ping:
        parser.print_help()
        return
    s = get_settings().llm
    reply = get_llm(s).chat([{"role": "user", "content": "Reply with just: pong"}], max_tokens=10)
    print(f"[{s.provider}:{s.chat_model}] {reply}")


if __name__ == "__main__":
    main()

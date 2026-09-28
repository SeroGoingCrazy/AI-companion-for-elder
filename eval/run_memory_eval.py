"""L2 memory-extraction eval (DEV_SPEC 4.2).

Usage: uv run python eval/run_memory_eval.py [--cases eval/memory_cases.yaml] [-v]
Needs OPENAI_API_KEY (LLM_PROVIDER=mock also works, as a smoke test of the harness).
Scoring: every expected (kind, subject) found and nothing outside expected + optional, with
subjects compared case-insensitively (kinds in `subject_free` compare by kind only), and
`requested` matching `expect_privacy`. An expected subject may be a list of accepted names
(synonyms of the same kind); any one of them satisfies it.
Exit code 1 when accuracy < 90% or privacy-request recall < 100%.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml

from elder_companion.llm import LLMError, get_llm
from elder_companion.memory.extractor import MemoryExtractor, Turn, normalize_subject
from elder_companion.settings import get_settings

DEFAULT_CASES = Path(__file__).with_name("memory_cases.yaml")
MIN_ACCURACY = 0.9
FIRST_ID = 100


@dataclass
class CaseResult:
    say: str
    expected: list[frozenset[tuple[str, str]]]  # one set of accepted keys per expected item
    got: set[tuple[str, str]]
    optional: frozenset[tuple[str, str]]
    expect_privacy: bool
    got_privacy: bool
    error: str | None = None

    @property
    def items_ok(self) -> bool:
        allowed = self.optional.union(*self.expected)
        return all(accepted & self.got for accepted in self.expected) and self.got <= allowed

    @property
    def ok(self) -> bool:
        return self.error is None and self.items_ok and self.expect_privacy == self.got_privacy


def _key(kind: str, subject: str, subject_free: set[str]) -> tuple[str, str]:
    return kind, "*" if kind in subject_free else normalize_subject(subject)


def run_case(extractor: MemoryExtractor, case: dict) -> CaseResult:
    free = set(case.get("subject_free", []))
    turns = [
        Turn(FIRST_ID + i, role, text) for i, (role, text) in enumerate(case.get("context", []))
    ]
    current = FIRST_ID + len(turns)
    expected = [
        frozenset(_key(k, name, free) for name in (s if isinstance(s, list) else [s]))
        for k, s in case.get("expect_items", [])
    ]
    optional = frozenset(_key(k, s, free) for k, s in case.get("optional", []))
    want_privacy = bool(case.get("expect_privacy", False))
    try:
        result = extractor.extract(current, case["say"], turns)
    except LLMError as e:
        return CaseResult(case["say"], expected, set(), optional, want_privacy, False, str(e))
    got = {_key(i.kind, i.subject, free) for i in result.items}
    return CaseResult(
        case["say"], expected, got, optional, want_privacy, result.privacy_request.requested
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("-v", "--verbose", action="store_true", help="show passing cases too")
    args = parser.parse_args(argv)

    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    settings = get_settings()
    extractor = MemoryExtractor(get_llm(settings.llm))
    print(f"provider={settings.llm.provider} model={settings.llm.extract_model} cases={len(cases)}")

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: run_case(extractor, c), cases))
    elapsed = time.perf_counter() - start

    for i, r in enumerate(results, 1):
        if r.ok and not args.verbose:
            continue
        mark = "PASS" if r.ok else "FAIL"
        detail = r.error or (
            f"expected={[sorted(a) for a in r.expected]} got={sorted(r.got)} "
            f"privacy expected={r.expect_privacy} got={r.got_privacy}"
        )
        print(f"[{mark}] #{i:02d} {r.say}\n        {detail}")

    passed = sum(r.ok for r in results)
    accuracy = passed / len(results)
    wanted = [r for r in results if r.expect_privacy]
    found = sum(r.got_privacy for r in wanted)
    recall = found / len(wanted) if wanted else 1.0
    false_requests = sum(r.got_privacy and not r.expect_privacy for r in results)
    print(
        f"\naccuracy {passed}/{len(results)} = {accuracy:.0%} (target >= {MIN_ACCURACY:.0%})"
        f"\nprivacy-request recall {found}/{len(wanted)} = {recall:.0%} (target 100%)"
        f"\nfalse privacy requests {false_requests}"
        f"\nelapsed {elapsed:.1f}s"
    )
    return 0 if accuracy >= MIN_ACCURACY and recall == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())

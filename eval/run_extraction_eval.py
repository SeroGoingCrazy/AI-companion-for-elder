"""L2 symptom-extraction eval (DEV_SPEC 4.2).

Usage: uv run python eval/run_extraction_eval.py [--cases eval/extraction_cases.yaml] [-v]
Needs OPENAI_API_KEY (LLM_PROVIDER=mock also works, as a smoke test of the harness).
Scoring: every `expect` canonical found and nothing outside `expect` + `optional`.
Exit code 1 when accuracy < 90% or red-flag recall < 100%.
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
from elder_companion.settings import get_settings
from elder_companion.symptoms.extractor import SymptomExtractor
from elder_companion.symptoms.schema import get_catalog

DEFAULT_CASES = Path(__file__).with_name("extraction_cases.yaml")
MIN_ACCURACY = 0.9


@dataclass
class CaseResult:
    say: str
    expected: set[str]
    got: set[str]
    optional: frozenset[str] = frozenset()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.expected <= self.got <= self.expected | self.optional


def run_case(extractor: SymptomExtractor, case: dict) -> CaseResult:
    context = [{"role": role, "content": text} for role, text in case.get("context", [])]
    expected = set(case.get("expect", []))
    optional = frozenset(case.get("optional", []))
    try:
        items = extractor.extract(case["say"], context)
    except LLMError as e:
        return CaseResult(case["say"], expected, set(), optional, error=str(e))
    return CaseResult(case["say"], expected, {i.canonical for i in items}, optional)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("-v", "--verbose", action="store_true", help="show passing cases too")
    args = parser.parse_args(argv)

    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    catalog = get_catalog()
    settings = get_settings()
    extractor = SymptomExtractor(get_llm(settings.llm), catalog)
    print(f"provider={settings.llm.provider} model={settings.llm.extract_model} cases={len(cases)}")

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: run_case(extractor, c), cases))
    elapsed = time.perf_counter() - start

    for i, r in enumerate(results, 1):
        if r.ok and not args.verbose:
            continue
        mark = "PASS" if r.ok else "FAIL"
        detail = r.error or f"expected={sorted(r.expected)} got={sorted(r.got)}"
        print(f"[{mark}] #{i:02d} {r.say}\n        {detail}")

    passed = sum(r.ok for r in results)
    accuracy = passed / len(results)
    flags_expected = sum(len({c for c in r.expected if catalog.is_red_flag(c)}) for r in results)
    flags_found = sum(
        len({c for c in r.expected & r.got if catalog.is_red_flag(c)}) for r in results
    )
    recall = flags_found / flags_expected if flags_expected else 1.0
    false_flags = sum(
        len({c for c in r.got - r.expected - r.optional if catalog.is_red_flag(c)}) for r in results
    )

    print(
        f"\naccuracy {passed}/{len(results)} = {accuracy:.0%} (target >= {MIN_ACCURACY:.0%})"
        f"\nred-flag recall {flags_found}/{flags_expected} = {recall:.0%} (target 100%)"
        f"\nfalse red flags {false_flags}"
        f"\nelapsed {elapsed:.1f}s"
    )
    return 0 if accuracy >= MIN_ACCURACY and recall == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())

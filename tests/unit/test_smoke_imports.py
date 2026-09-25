import importlib

import pytest

MODULES = [
    "elder_companion",
    "elder_companion.llm",
    "elder_companion.chat",
    "elder_companion.symptoms",
    "elder_companion.alerts",
    "elder_companion.web",
    "fall_detector",
]


@pytest.mark.unit
@pytest.mark.parametrize("name", MODULES)
def test_import(name: str) -> None:
    importlib.import_module(name)

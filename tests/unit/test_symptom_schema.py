from typing import Any

import pytest
from pydantic import ValidationError

from elder_companion.symptoms.schema import (
    SymptomCatalog,
    SymptomDef,
    extraction_schema,
    get_catalog,
    parse_extraction,
)

pytestmark = pytest.mark.unit

RED_FLAGS = {
    "chest_pain",
    "shortness_of_breath",
    "numbness_weakness",
    "slurred_speech",
    "confusion",
    "fall",
    "self_harm",
}

ITEM = {
    "canonical": "dizziness",
    "label": "morning dizziness",
    "body_part": "head",
    "severity": "mild",
    "duration": "2 days",
    "onset": "when getting up in the morning",
    "status": "new",
    "raw_quote": "头有点晕",
}


def _walk_objects(node: Any):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_objects(v)


def test_catalog_loads_spec_symptoms() -> None:
    catalog = get_catalog()
    assert {d.canonical for d in catalog if d.red_flag} == RED_FLAGS
    assert "other" in catalog and "dizziness" in catalog
    assert catalog.get("chest_pain").display == "Chest pain / 胸痛胸闷"
    assert all(d.en and d.zh and d.hint for d in catalog)


def test_catalog_rejects_duplicates_and_missing_other() -> None:
    d = SymptomDef("cough", "Cough", "咳嗽", False)
    with pytest.raises(ValueError, match="duplicate"):
        SymptomCatalog([d, d, SymptomDef("other", "Other", "其他", False)])
    with pytest.raises(ValueError, match="other"):
        SymptomCatalog([d])


def test_valid_extraction_parses() -> None:
    parsed = parse_extraction({"symptoms": [ITEM]})
    assert parsed.symptoms[0].canonical == "dizziness"
    assert parse_extraction({"symptoms": []}).symptoms == []


@pytest.mark.parametrize(
    "patch",
    [
        {"canonical": "broken_heart"},
        {"severity": "terrible"},
        {"status": "gone"},
        {"surprise": "field"},
    ],
)
def test_invalid_item_rejected(patch: dict) -> None:
    with pytest.raises(ValidationError):
        parse_extraction({"symptoms": [{**ITEM, **patch}]})


def test_missing_field_rejected() -> None:
    item = {k: v for k, v in ITEM.items() if k != "duration"}
    with pytest.raises(ValidationError):
        parse_extraction({"symptoms": [item]})


def test_custom_catalog_via_context() -> None:
    catalog = SymptomCatalog(
        [
            SymptomDef("hiccups", "Hiccups", "打嗝", False),
            SymptomDef("other", "Other", "其他", False),
        ]
    )
    assert parse_extraction({"symptoms": [{**ITEM, "canonical": "hiccups"}]}, catalog)
    with pytest.raises(ValidationError):
        parse_extraction({"symptoms": [ITEM]}, catalog)


def test_schema_is_strict_mode_compatible() -> None:
    schema = extraction_schema()
    objects = list(_walk_objects(schema))
    assert len(objects) == 2  # root + SymptomItem
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    assert "default" not in str(schema)


def test_schema_enum_comes_from_catalog() -> None:
    item = extraction_schema()["$defs"]["SymptomItem"]["properties"]
    assert item["canonical"]["enum"] == get_catalog().names
    assert item["severity"]["enum"] == ["mild", "moderate", "severe", "unknown"]
    # the exported schema is a copy; mutating it must not leak into the next call
    item["canonical"]["enum"].append("x")
    assert "x" not in extraction_schema()["$defs"]["SymptomItem"]["properties"]["canonical"]["enum"]

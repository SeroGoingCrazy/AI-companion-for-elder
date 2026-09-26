"""Symptom catalog (config/symptoms.yaml) and the extractor's structured-output contract."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from elder_companion.settings import PROJECT_ROOT

DEFAULT_CATALOG_PATH = PROJECT_ROOT / "config" / "symptoms.yaml"
SCHEMA_NAME = "symptom_extraction"

Severity = Literal["mild", "moderate", "severe", "unknown"]
Status = Literal["new", "ongoing", "improved", "resolved"]
SEVERITY_ORDER: tuple[Severity, ...] = ("unknown", "mild", "moderate", "severe")


@dataclass(frozen=True)
class SymptomDef:
    canonical: str
    en: str
    zh: str
    red_flag: bool
    hint: str = ""

    @property
    def display(self) -> str:
        return f"{self.en} / {self.zh}"


class SymptomCatalog:
    def __init__(self, defs: list[SymptomDef]) -> None:
        if not defs:
            raise ValueError("symptom catalog is empty")
        self._defs = {d.canonical: d for d in defs}
        if len(self._defs) != len(defs):
            raise ValueError("duplicate canonical in symptom catalog")
        if "other" not in self._defs:
            raise ValueError("symptom catalog must define 'other'")

    @classmethod
    def load(cls, path: Path = DEFAULT_CATALOG_PATH) -> SymptomCatalog:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls([SymptomDef(**entry) for entry in raw.get("symptoms", [])])

    @property
    def names(self) -> list[str]:
        return list(self._defs)

    def __contains__(self, canonical: object) -> bool:
        return canonical in self._defs

    def __iter__(self):
        return iter(self._defs.values())

    def get(self, canonical: str) -> SymptomDef:
        return self._defs[canonical]

    def is_red_flag(self, canonical: str) -> bool:
        d = self._defs.get(canonical)
        return bool(d and d.red_flag)


@lru_cache
def get_catalog() -> SymptomCatalog:
    return SymptomCatalog.load()


class _Strict(BaseModel):
    # extra=forbid emits additionalProperties: false, which OpenAI strict mode requires.
    model_config = ConfigDict(extra="forbid")


class SymptomItem(_Strict):
    # No field has a default: strict mode needs every property listed in `required`;
    # optional values are expressed as nullable instead.
    canonical: str = Field(description="One value from the symptom list")
    label: str = Field(description="Short description in the elder's own terms")
    body_part: str | None
    severity: Severity
    duration: str | None = Field(description="How long it has been going on, e.g. '2 days'")
    onset: str | None = Field(description="When it happens or started, e.g. 'mornings'")
    status: Status
    raw_quote: str = Field(description="Exact words copied from the elder's utterance")

    @field_validator("canonical")
    @classmethod
    def _known_canonical(cls, v: str, info: ValidationInfo) -> str:
        catalog = (info.context or {}).get("catalog") or get_catalog()
        if v not in catalog:
            raise ValueError(f"unknown symptom canonical {v!r}")
        return v


class SymptomExtraction(_Strict):
    symptoms: list[SymptomItem]


def extraction_schema(catalog: SymptomCatalog | None = None) -> dict[str, Any]:
    """JSON Schema for Structured Outputs, with `canonical` restricted to the catalog's enum."""
    catalog = catalog or get_catalog()
    schema = copy.deepcopy(SymptomExtraction.model_json_schema())
    schema["$defs"]["SymptomItem"]["properties"]["canonical"]["enum"] = catalog.names
    return schema


def parse_extraction(
    data: dict[str, Any], catalog: SymptomCatalog | None = None
) -> SymptomExtraction:
    return SymptomExtraction.model_validate(data, context={"catalog": catalog or get_catalog()})

"""Alert rules for symptoms (spec 3.4.3). Pure: levels come from config/symptoms.yaml."""

from __future__ import annotations

from datetime import datetime, timedelta

from elder_companion.alerts.schemas import AlertLevel
from elder_companion.symptoms.schema import SymptomCatalog, get_catalog


def alert_level(
    canonical: str, severity: str, status: str = "new", catalog: SymptomCatalog | None = None
) -> AlertLevel | None:
    """Red flags -> high, always (even "I fell, but I'm fine now" is worth telling family).
    Other symptoms -> medium when severe and not yet resolved; otherwise no alert."""
    catalog = catalog or get_catalog()
    if catalog.is_red_flag(canonical):
        return "high"
    if severity == "severe" and status != "resolved":
        return "medium"
    return None


def should_alert(
    last_alert_at: datetime | None, now: datetime, debounce: timedelta = timedelta(hours=2)
) -> bool:
    """Debounce: one alert per symptom per `debounce` window."""
    return last_alert_at is None or now - last_alert_at >= debounce

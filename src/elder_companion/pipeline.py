"""Background work after each elder message (spec 5.4): symptoms, then companion memory.

The two extractions are independent (ADR 17): either can fail without affecting the other.
Symptoms go first so a red-flag alert reaches the family as fast as possible; its path never
looks at privacy marks (ADR 19), so the order cannot hide an emergency.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from elder_companion.alerts.bus import AlertBus, StreamEvent
from elder_companion.alerts.schemas import AlertOut
from elder_companion.memory.extractor import MemoryExtractor
from elder_companion.memory.store import process_message_memory
from elder_companion.settings import Settings
from elder_companion.symptoms.extractor import SymptomExtractor
from elder_companion.symptoms.service import run_symptom_pipeline


def process_elder_message(
    session_factory: sessionmaker[Session],
    symptom_extractor: SymptomExtractor,
    memory_extractor: MemoryExtractor,
    settings: Settings,
    bus: AlertBus | None,
    message_id: int,
) -> list[AlertOut]:
    """Never raises. Publishes alerts as soon as they exist, then one `activity` event once
    memory (and any privacy request) has been applied, so the dashboard reloads a view that
    already hides what she asked to keep private."""
    outs, symptoms = run_symptom_pipeline(
        session_factory, symptom_extractor, settings.symptoms, bus, message_id
    )
    process_message_memory(session_factory, memory_extractor, settings, message_id)
    if bus is not None:
        bus.publish(StreamEvent.activity(message_id, symptoms))
    return outs

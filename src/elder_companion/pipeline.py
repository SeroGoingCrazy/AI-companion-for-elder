"""Background work after each elder message (spec 5.4): symptoms, the ask-before-sharing
consent step (consent.py), companion memory, then scrubbing personal details from the turn
for the family view (redaction.py).

The two extractions are independent (ADR 17): either can fail without affecting the other.
Symptoms go first so a red-flag alert reaches the family as fast as possible; its path never
looks at privacy marks (ADR 19), so the order cannot hide an emergency.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session, sessionmaker

from elder_companion.alerts.bus import AlertBus, StreamEvent
from elder_companion.alerts.schemas import AlertOut
from elder_companion.consent import ConsentExtractor, ConsentService
from elder_companion.memory.extractor import MemoryExtractor
from elder_companion.memory.store import process_message_memory
from elder_companion.models import Alert
from elder_companion.privacy import alert_is_visible
from elder_companion.redaction import Redactor, redact_messages
from elder_companion.settings import Settings
from elder_companion.symptoms.extractor import SymptomExtractor
from elder_companion.symptoms.service import run_symptom_pipeline

logger = logging.getLogger(__name__)


def process_elder_message(
    session_factory: sessionmaker[Session],
    symptom_extractor: SymptomExtractor,
    memory_extractor: MemoryExtractor,
    settings: Settings,
    bus: AlertBus | None,
    redactor: Redactor | None,
    consent_extractor: ConsentExtractor | None,
    message_id: int,
    reply_id: int | None = None,
) -> list[AlertOut]:
    """Never raises. Red-flag (bypass-level) alerts are published as soon as they exist;
    other alerts only after the consent step, and only if she has agreed to share them.
    Then one `activity` event once memory (and any privacy request) has been applied, so the
    dashboard reloads a view that already hides what is not hers to see and shows the turn
    redacted."""
    bypass = settings.privacy.bypass_levels
    asking = consent_extractor is not None and settings.privacy.ask_before_sharing
    outs, symptoms = run_symptom_pipeline(
        session_factory,
        symptom_extractor,
        settings.symptoms,
        bus,
        message_id,
        publish_now=(lambda out: out.level in bypass) if asking else None,
    )
    if asking:
        deferred = [o for o in outs if o.level not in bypass]
        try:
            with session_factory() as session:
                ConsentService(session, consent_extractor).process_message(message_id)
                visible = [
                    o
                    for o in deferred
                    if alert_is_visible(session, session.get_one(Alert, o.id), bypass)
                ]
        except Exception:
            logger.exception("consent step crashed for message %s", message_id)
            visible = []
        if bus is not None:
            for out in visible:
                bus.publish(StreamEvent.alert(out))
    process_message_memory(session_factory, memory_extractor, settings, message_id)
    if redactor is not None:
        redact_messages(session_factory, redactor, [message_id, reply_id])
    if bus is not None:
        bus.publish(StreamEvent.activity(message_id, symptoms))
    return outs

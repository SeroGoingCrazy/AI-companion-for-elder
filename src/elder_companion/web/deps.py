from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import partial
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from elder_companion.alerts.bus import AlertBus
from elder_companion.chat.service import ChatService
from elder_companion.pipeline import process_elder_message


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_chat_service(request: Request, session: SessionDep) -> ChatService:
    settings = request.app.state.settings
    return ChatService(
        session,
        request.app.state.llm,
        settings.chat,
        audio_dir=settings.paths.audio_dir,
        agenda=settings.agenda,
        memory=settings.memory,
    )


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


def get_alert_bus(request: Request) -> AlertBus:
    return request.app.state.alert_bus


AlertBusDep = Annotated[AlertBus, Depends(get_alert_bus)]


def get_symptom_pipeline(request: Request) -> Callable[[int], object]:
    """`pipeline(user_message_id)`: run as a BackgroundTask after a chat turn (symptoms, then
    companion memory)."""
    state = request.app.state
    return partial(
        process_elder_message,
        state.sessionmaker,
        state.symptom_extractor,
        state.memory_extractor,
        state.settings,
        state.alert_bus,
    )


SymptomPipelineDep = Annotated[Callable[[int], object], Depends(get_symptom_pipeline)]

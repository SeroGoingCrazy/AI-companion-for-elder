from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from elder_companion.chat.service import ChatService


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_chat_service(request: Request, session: SessionDep) -> ChatService:
    settings = request.app.state.settings
    return ChatService(
        session, request.app.state.llm, settings.chat, audio_dir=settings.paths.audio_dir
    )


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]

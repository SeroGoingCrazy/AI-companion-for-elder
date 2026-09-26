from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from elder_companion.chat.service import ChatResult
from elder_companion.elders import ElderNotFound
from elder_companion.web.deps import ChatServiceDep

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    elder_id: int | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v.strip()


class GreetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elder_id: int | None = None


class ChatOut(BaseModel):
    message_id: int
    user_text: str
    reply_text: str
    fallback: bool

    @classmethod
    def of(cls, r: ChatResult) -> ChatOut:
        return cls(
            message_id=r.message_id,
            user_text=r.user_text,
            reply_text=r.reply_text,
            fallback=r.fallback,
        )


@router.post("", response_model=ChatOut)
def chat(body: ChatIn, service: ChatServiceDep) -> ChatOut:
    """One text turn. (Audio uploads to this endpoint arrive in stage C.)"""
    try:
        return ChatOut.of(service.reply(body.elder_id, body.text))
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/greet", response_model=ChatOut)
def greet(service: ChatServiceDep, body: GreetIn | None = None) -> ChatOut:
    """Opening line when the elder app starts; body is optional."""
    try:
        return ChatOut.of(service.greet(body.elder_id if body else None))
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

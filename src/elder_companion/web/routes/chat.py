from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from elder_companion.chat.service import ChatResult, MessageNotFound
from elder_companion.elders import ElderNotFound
from elder_companion.redaction import redact_messages
from elder_companion.web.deps import ChatServiceDep, SymptomPipelineDep

router = APIRouter(prefix="/api/chat", tags=["chat"])
tts_router = APIRouter(prefix="/api/tts", tags=["chat"])

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # ~10 min of opus; a 60s clip is well under 1 MB

# The transcription API infers the format from the file extension, and browsers upload
# MediaRecorder blobs with names like "blob", so derive the extension from the content type.
_AUDIO_EXTENSIONS = {
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/mp4": "mp4",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/ogg": "ogg",
}


def audio_filename(content_type: str | None) -> str:
    """'audio/webm;codecs=opus' -> 'speech.webm' (defaults to webm, Chrome's format)."""
    base = (content_type or "").split(";")[0].strip().lower()
    return f"speech.{_AUDIO_EXTENSIONS.get(base, 'webm')}"


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
    message_id: int | None
    user_text: str
    reply_text: str
    fallback: bool = False
    need_retry: bool = False

    @classmethod
    def of(cls, r: ChatResult) -> ChatOut:
        return cls(
            message_id=r.message_id,
            user_text=r.user_text,
            reply_text=r.reply_text,
            fallback=r.fallback,
            need_retry=r.need_retry,
        )


def schedule_symptoms(
    result: ChatResult, tasks: BackgroundTasks, pipeline: SymptomPipelineDep
) -> None:
    """Extract symptoms (and redact the turn for the family) after the response is sent, so
    chat latency is unaffected."""
    if result.user_message_id is not None:
        tasks.add_task(pipeline, result.user_message_id, result.message_id)


@router.post("", response_model=ChatOut)
def chat(
    body: ChatIn, service: ChatServiceDep, tasks: BackgroundTasks, pipeline: SymptomPipelineDep
) -> ChatOut:
    """One text turn."""
    try:
        result = service.reply(body.elder_id, body.text)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    schedule_symptoms(result, tasks, pipeline)
    return ChatOut.of(result)


@router.post("/audio", response_model=ChatOut)
def chat_audio(
    service: ChatServiceDep,
    tasks: BackgroundTasks,
    pipeline: SymptomPipelineDep,
    audio: Annotated[UploadFile, File(description="MediaRecorder clip (webm/mp4/wav/...)")],
    elder_id: Annotated[int | None, Form()] = None,
) -> ChatOut:
    """One voice turn: transcribe, then reply. `need_retry: true` means "please say it again"."""
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="audio file is empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio file is too large")
    try:
        result = service.reply_audio(elder_id, data, filename=audio_filename(audio.content_type))
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    schedule_symptoms(result, tasks, pipeline)
    return ChatOut.of(result)


@router.post("/greet", response_model=ChatOut)
def greet(
    request: Request, service: ChatServiceDep, tasks: BackgroundTasks, body: GreetIn | None = None
) -> ChatOut:
    """Opening line when the elder app starts; body is optional."""
    try:
        result = service.greet(body.elder_id if body else None)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    state = request.app.state
    tasks.add_task(redact_messages, state.sessionmaker, state.redactor, [result.message_id])
    return ChatOut.of(result)


@tts_router.get(
    "/{message_id}",
    response_class=FileResponse,
    responses={200: {"content": {"audio/mpeg": {}}}, 204: {"description": "TTS unavailable"}},
)
def tts(
    message_id: int,
    request: Request,
    service: ChatServiceDep,
    voice: str | None = Query(None, description="a key of llm.tts_voices, e.g. female / male"),
) -> Response:
    """mp3 of an assistant reply (generated once per voice, then cached). 204 = speak/show text
    instead."""
    voices = request.app.state.settings.llm.tts_voices
    if voice is not None and voice not in voices:
        raise HTTPException(status_code=422, detail=f"voice must be one of {sorted(voices)}")
    try:
        path = service.synthesize(message_id, voices[voice] if voice else None)
    except MessageNotFound as e:
        raise HTTPException(
            status_code=404, detail=f"assistant message {message_id} not found"
        ) from e
    if path is None:
        return Response(status_code=204)
    # no-cache = revalidate with the ETag every time. Message ids restart after a DB reset,
    # so a long max-age would make the browser replay stale audio for a new message.
    stat = path.stat()
    etag = f'"{path.stem}-{stat.st_mtime_ns}-{stat.st_size}"'
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="audio/mpeg", headers=headers)

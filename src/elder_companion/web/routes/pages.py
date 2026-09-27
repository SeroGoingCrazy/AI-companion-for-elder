from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from elder_companion.elders import ElderNotFound, get_elder
from elder_companion.family_loop.claims import list_members
from elder_companion.i18n import LANG_COOKIE, available_langs, resolve, strings
from elder_companion.web.deps import SessionDep

WEB_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter(include_in_schema=False)


def _lang(request: Request, lang: str | None) -> str:
    """`?lang=` pins it for a demo link, the cookie remembers it, the browser hints."""
    return resolve(lang, request.cookies.get(LANG_COOKIE), request.headers.get("accept-language"))


def _other(lang: str) -> str:
    """The language the switch offers. With two shipped, it is simply the other one."""
    langs = available_langs()
    return langs[(langs.index(lang) + 1) % len(langs)]


@router.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/elder")


@router.get("/elder", response_class=HTMLResponse)
def elder_page(
    request: Request, session: SessionDep, elder_id: int | None = None, lang: str | None = None
) -> HTMLResponse:
    try:
        elder = get_elder(session, elder_id)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    settings = request.app.state.settings
    companion_name = settings.chat.companion_name
    code = _lang(request, lang)
    t = strings(code)
    return templates.TemplateResponse(
        request,
        "elder.html",
        {
            "companion_name": companion_name,
            "nickname": elder.nickname,
            "lang": code,
            "other_lang": _other(code),
            "t": t,
            "app_config": {
                "elderId": elder.id,
                "nickname": elder.nickname,
                "voices": list(settings.llm.tts_voices),
                "lang": code,
                # The scripts render their own status text, so they need the same table.
                "t": t["elder"],
                # Spoken back to her, so it follows the language she just used rather than
                # the language the interface happens to be in.
                "spoken": {
                    code: strings(code)["elder"]["not_caught"] for code in available_langs()
                },
                "companionName": companion_name,
            },
        },
    )


def pick_member(members: list, member: str | None) -> int | None:  # noqa: ANN001
    """`?member=` takes an id or a name ("ben"); None lets the page use the remembered one."""
    if not member:
        return None
    for m in members:
        if member == str(m.id) or member.casefold() == m.name.casefold():
            return m.id
    return None


@router.get("/family", response_class=HTMLResponse)
def family_page(
    request: Request,
    session: SessionDep,
    elder_id: int | None = None,
    member: str | None = None,
    lang: str | None = None,
) -> HTMLResponse:
    try:
        elder = get_elder(session, elder_id)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    settings = request.app.state.settings
    members = list_members(session, elder.id)
    code = _lang(request, lang)
    t = strings(code)
    return templates.TemplateResponse(
        request,
        "family.html",
        {
            "elder": elder,
            "companion_name": settings.chat.companion_name,
            "lang": code,
            "other_lang": _other(code),
            "t": t,
            "app_config": {
                "elderId": elder.id,
                "nickname": elder.nickname,
                "timezone": settings.chat.timezone,
                "lang": code,
                "t": t["family"],
                "severity": t["severity"],
                "status": t["status"],
                "companionName": settings.chat.companion_name,
                # The camera is proxied at /fall/stream (web/routes/fall_proxy.py) rather
                # than reached on this port directly, but the port stays in the config so
                # anything still pointing at fall-mcp itself keeps working.
                "fallStreamPort": settings.fall.mcp.port,
                "members": [{"id": m.id, "name": m.name, "relation": m.relation} for m in members],
                "memberId": pick_member(members, member),
            },
        },
    )

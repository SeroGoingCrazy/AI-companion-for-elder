from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from elder_companion.elders import ElderNotFound, get_elder
from elder_companion.family_loop.claims import list_members
from elder_companion.web.deps import SessionDep

WEB_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter(include_in_schema=False)


@router.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/elder")


@router.get("/elder", response_class=HTMLResponse)
def elder_page(request: Request, session: SessionDep, elder_id: int | None = None) -> HTMLResponse:
    try:
        elder = get_elder(session, elder_id)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    companion_name = request.app.state.settings.chat.companion_name
    return templates.TemplateResponse(
        request,
        "elder.html",
        {
            "companion_name": companion_name,
            "nickname": elder.nickname,
            "app_config": {"elderId": elder.id, "nickname": elder.nickname},
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
    request: Request, session: SessionDep, elder_id: int | None = None, member: str | None = None
) -> HTMLResponse:
    try:
        elder = get_elder(session, elder_id)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    settings = request.app.state.settings
    members = list_members(session, elder.id)
    return templates.TemplateResponse(
        request,
        "family.html",
        {
            "elder": elder,
            "companion_name": settings.chat.companion_name,
            "app_config": {
                "elderId": elder.id,
                "nickname": elder.nickname,
                "timezone": settings.chat.timezone,
                # fall-mcp's MJPEG view; the page uses its own hostname with this port
                "fallStreamPort": settings.fall.mcp.port,
                "members": [{"id": m.id, "name": m.name, "relation": m.relation} for m in members],
                "memberId": pick_member(members, member),
            },
        },
    )

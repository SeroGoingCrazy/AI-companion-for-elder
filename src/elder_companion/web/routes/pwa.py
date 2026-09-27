"""Progressive-web-app plumbing: the two manifests and the service worker.

Both surfaces install separately — the elder installs "Sunny" on their phone, the family
installs the dashboard on theirs — so each gets its own manifest with its own `id` and
`start_url`. The manifests are generated rather than static because the companion name is
configurable (`chat.companion_name`).

The service worker is served from the site root, not from /static, because a worker's
default scope is its own directory and it has to control both /elder and /family.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from elder_companion.web.routes.pages import WEB_DIR

router = APIRouter(include_in_schema=False)

MANIFEST_MEDIA_TYPE = "application/manifest+json"
SKY = "#e8f0f7"  # --sky, matches the <meta name="theme-color"> in both templates


def _icon(size: int, *, maskable: bool) -> dict[str, str]:
    stem = "maskable" if maskable else "icon"
    return {
        "src": f"/static/icons/{stem}-{size}.png",
        "sizes": f"{size}x{size}",
        "type": "image/png",
        "purpose": "maskable" if maskable else "any",
    }


# Android needs both: "any" is used as drawn, "maskable" is cropped to the launcher's shape.
_ICONS = [_icon(s, maskable=m) for m in (False, True) for s in (192, 512)]


def _manifest(
    *, app_id: str, name: str, short_name: str, description: str, start_url: str, orientation: str
) -> dict[str, Any]:
    return {
        "id": app_id,
        "name": name,
        "short_name": short_name,
        "description": description,
        "start_url": start_url,
        "scope": "/",
        "display": "standalone",
        "orientation": orientation,
        "background_color": SKY,
        "theme_color": SKY,
        "lang": "en",
        "dir": "ltr",
        "categories": ["health", "lifestyle"],
        "icons": _ICONS,
    }


@router.get("/manifest-elder.webmanifest")
def elder_manifest(request: Request) -> JSONResponse:
    companion = request.app.state.settings.chat.companion_name
    return JSONResponse(
        _manifest(
            app_id="/?app=elder",
            name=f"{companion} — your companion",
            short_name=companion,
            description=f"Talk with {companion}. Hold the sun to speak; {companion} speaks back.",
            start_url="/elder",
            # The elder app is one big column built around the talk button; landscape helps nobody.
            orientation="portrait",
        ),
        media_type=MANIFEST_MEDIA_TYPE,
    )


@router.get("/manifest-family.webmanifest")
def family_manifest(request: Request) -> JSONResponse:
    companion = request.app.state.settings.chat.companion_name
    return JSONResponse(
        _manifest(
            app_id="/?app=family",
            name=f"{companion} Care — family dashboard",
            short_name=f"{companion} Care",
            description="Daily summary, symptom timeline, alerts and the live fall-detection view.",
            start_url="/family",
            # The dashboard is a grid; it reads fine on a tablet held either way.
            orientation="any",
        ),
        media_type=MANIFEST_MEDIA_TYPE,
    )


@router.get("/sw.js")
def service_worker() -> Response:
    """Serve the worker from the root so its scope covers /elder and /family."""
    body = (WEB_DIR / "static" / "sw.js").read_bytes()
    return Response(
        body,
        media_type="text/javascript",
        headers={
            # The worker script itself must not be cached, or a stale one pins the app.
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )

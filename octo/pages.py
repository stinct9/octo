"""The two pages about Octo itself, served by the lab so they open without signing in anywhere.

  GET /about   the landing page for people who ask what Octo does (docs/landing), built on every
               visit, so the owner's principles on it are always the rubric's words. On the paired phone too.
               A copy without docs/landing (the public one) sends it to the published page.
  GET /case    the recorder case's design review (hardware/case). Rebuilt when its sources are newer
               than the last build and manifold3d is installed; otherwise the copy published on
               claude.ai, which is republished whenever the design changes.

Both pages are written as the body of a claude.ai artifact; document() makes them complete pages.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from . import ROOT

LANDING_BUILD = ROOT / "docs" / "landing" / "build.py"
CASE = ROOT / "hardware" / "case"
CASE_PAGE = CASE / "out" / "octo-case.html"
CASE_SOURCES = [CASE / "case.py", CASE / "viewer.html", ROOT / "firmware" / "recorder" / "octo_display.js"]
CASE_PUBLISHED = "https://claude.ai/artifact/34PcDpmUzvn1zrZv1ExBsR"
LANDING_PUBLISHED = "https://octolab.online"
NO_STORE = {"Cache-Control": "no-store"}
_case_lock = threading.Lock()


def document(body: str) -> str:
    """An artifact's body as a complete page: what claude.ai adds around it when it publishes."""
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '<style>:root{color-scheme:light}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
            f'</head>\n<body>\n{body}\n</body>\n</html>\n')


def landing_page() -> str:
    """The landing page, assembled now from docs/landing (the build script, loaded fresh each time)."""
    spec = importlib.util.spec_from_file_location("octo_landing_build", LANDING_BUILD)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    page, _, _ = build.build()
    return document(page)


def case_is_current() -> bool:
    """A built case page exists and nothing it was made from has changed since."""
    if not CASE_PAGE.exists():
        return False
    built = CASE_PAGE.stat().st_mtime
    return all(p.stat().st_mtime <= built for p in CASE_SOURCES if p.exists())


def can_build_case() -> bool:
    return importlib.util.find_spec("manifold3d") is not None


def build_case() -> None:
    """Run hardware/case/case.py (about 2 s); a failure leaves the page out of date."""
    subprocess.run([sys.executable, str(CASE / "case.py")], cwd=ROOT, capture_output=True, timeout=180, check=False)


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/about", response_model=None)
    def about() -> HTMLResponse | RedirectResponse:
        if not LANDING_BUILD.exists():
            return RedirectResponse(LANDING_PUBLISHED, status_code=307)
        return HTMLResponse(landing_page(), headers=NO_STORE)

    @router.get("/case", response_model=None)
    def case() -> HTMLResponse | RedirectResponse:
        with _case_lock:
            if not case_is_current() and can_build_case():
                build_case()
            if case_is_current():
                return HTMLResponse(document(CASE_PAGE.read_text()), headers=NO_STORE)
        return RedirectResponse(CASE_PUBLISHED, status_code=307)

    return router

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from text_evolver.config import AppSettings
from text_evolver.db.models import UserAccount
from text_evolver.services import job_file_counts, latest_job, user_view
from text_evolver.web.auth import get_or_create_csrf
from text_evolver.web.i18n import BROWSER_MESSAGES, catalog, plural, translate
from text_evolver.web.routines.flashes import pop_flashes


def template_context(request: Request, **values: object) -> dict[str, object]:
    return {
        "request": request,
        "csrf_token": get_or_create_csrf(request),
        "translate": translate,
        "ngettext": plural,
        "locale": request.scope.get("locale", "en"),
        "browser_messages": {message: translate(message) for message in BROWSER_MESSAGES},
        "file_count_messages": {
            category: catalog(request.scope.get("locale", "en")).ngettext(
                "%(count)s file selected", "%(count)s files selected", number
            )
            for category, number in {"one": 1, "few": 2, "many": 5, "other": 5}.items()
        },
        "google_enabled": bool(
            request.app.state.settings.google_client_id
            and request.app.state.settings.google_client_secret.get_secret_value()
        ),
        "get_flashed_messages": lambda: pop_flashes(request),
        **values,
    }


def render(request: Request, name: str, **values: object) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=name,
        context=template_context(request, **values),
    )


def navigation_context(session: Session, user: UserAccount, app_settings: AppSettings) -> dict[str, object]:
    job = latest_job(session, user.id)
    return {
        "user": user_view(session, user),
        "files_situation": job_file_counts(app_settings, job),
        "job": job,
    }

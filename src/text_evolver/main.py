from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from text_evolver.config import AppSettings, get_application_settings
from text_evolver.web.filters.access_log import configure_access_log
from text_evolver.web.i18n import translate
from text_evolver.web.middlewares.body_size import BodySizeLimitMiddleware
from text_evolver.web.middlewares.csrf import CSRFMiddleware
from text_evolver.web.middlewares.locale import LocaleMiddleware
from text_evolver.web.middlewares.sessions import DatabaseSessionMiddleware
from text_evolver.web.processing_status import ProcessingConnectionLimiter
from text_evolver.web.routers.accounts import router as account_router
from text_evolver.web.routers.base import router
from text_evolver.web.routers.google import router as google_router
from text_evolver.web.routers.health import router as health_router
from text_evolver.web.routers.passkeys import router as passkey_router
from text_evolver.web.routers.processing import router as processing_router
from text_evolver.web.routers.settings import router as settings_router
from text_evolver.web.sessions import CSRF_COOKIE

PACKAGE_ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.settings.ensure_directories()
    async with httpx.AsyncClient(timeout=10) as http_client:
        application.state.http_client = http_client
        yield


def create_app(settings: AppSettings | None = None) -> FastAPI:
    configure_access_log()
    settings = settings or get_application_settings()
    application = FastAPI(title="TextEvolver", lifespan=lifespan)
    application.state.settings = settings
    # Reverse registration order: signed cookie, database session, locale,
    # then body-size and CSRF validation before routing.
    application.add_middleware(CSRFMiddleware)
    application.add_middleware(BodySizeLimitMiddleware)
    application.add_middleware(LocaleMiddleware)
    application.add_middleware(
        DatabaseSessionMiddleware,
        cookie_secure=settings.cookie_secure,
    )
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=CSRF_COOKIE,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    application.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")
    application.state.templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
    application.state.websocket_limiter = ProcessingConnectionLimiter(settings.websocket_max_connections)
    application.include_router(router)
    application.include_router(health_router)
    application.include_router(settings_router)
    application.include_router(processing_router)
    application.include_router(account_router)
    application.include_router(google_router)
    application.include_router(passkey_router)

    @application.exception_handler(HTTPException)
    async def localized_exception(request: Request, exc: HTTPException):
        if "application/json" in request.headers.get("accept", ""):
            payload = {"detail": translate(str(exc.detail))}
            if exc.status_code == 428:
                payload["redirect"] = "/account/reauthenticate"
            return JSONResponse(payload, status_code=exc.status_code, headers=exc.headers)
        if exc.status_code == 428:
            from fastapi.responses import RedirectResponse

            return RedirectResponse("/account/reauthenticate", 303)
        if isinstance(exc.detail, str):
            exc.detail = translate(exc.detail)
        return await http_exception_handler(request, exc)

    return application


app = create_app()

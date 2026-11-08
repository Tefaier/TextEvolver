from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from text_evolver.api.middleware import APIRequestMiddleware
from text_evolver.api.routers import router as api_router
from text_evolver.config import AppSettings, get_application_settings
from text_evolver.image_storage import ImageStorageError
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
from text_evolver.web.validation_utils import ConflictError, ValidationError

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
    root = FastAPI(title="TextEvolver", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application = FastAPI(title="TextEvolver")
    api = FastAPI(title="TextEvolver API", version="1", redoc_url=None)
    # All surfaces share configured resources and test dependency overrides;
    # middleware stacks remain entirely separate.
    application.state = root.state
    api.state = root.state
    application.dependency_overrides = root.dependency_overrides
    api.dependency_overrides = root.dependency_overrides
    root.state.settings = settings
    root.state.web_app = application
    root.state.api_app = api
    api.add_middleware(BodySizeLimitMiddleware)
    api.add_middleware(APIRequestMiddleware)
    api.include_router(api_router)

    @api.exception_handler(ValidationError)
    async def api_validation_error(request: Request, exc: ValidationError):
        return JSONResponse({"detail": str(exc)}, status_code=409 if isinstance(exc, ConflictError) else 422)

    @api.exception_handler(ImageStorageError)
    async def api_storage_error(request: Request, exc: ImageStorageError):
        return JSONResponse({"detail": "Object storage is temporarily unavailable"}, status_code=503)

    @api.exception_handler(Exception)
    async def api_internal_error(request: Request, exc: Exception):
        return JSONResponse({"detail": "Internal server error"}, status_code=500,
                            headers={"Cache-Control": "no-store"})

    root.mount("/api/v1", api)
    root.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")
    root.include_router(health_router)
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
    application.state.templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
    application.state.websocket_limiter = ProcessingConnectionLimiter(settings.websocket_max_connections)
    application.include_router(router)
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

    root.mount("/", application)
    return root


app = create_app()

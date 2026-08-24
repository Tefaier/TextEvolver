from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from text_evolver.config import get_settings
from text_evolver.web.routes import router

PACKAGE_ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().ensure_directories()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="TextEvolver", lifespan=lifespan)
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="text_evolver_session",
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    application.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")
    application.state.templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
    application.include_router(router)
    return application


app = create_app()


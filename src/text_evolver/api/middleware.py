"""API request database lifecycle and early Bearer authentication."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from text_evolver.api.tokens import authenticate
from text_evolver.db.session import get_engine
from text_evolver.transactions import rollback_staged_jobs
from text_evolver.web.i18n import locale


class APIRequestMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Documentation describes the API without accessing account data.
        path = scope["path"].removeprefix(scope.get("root_path", ""))
        if path in {"/docs", "/openapi.json", "/docs/oauth2-redirect"}:
            return await self.app(scope, receive, send)
        factory = getattr(scope["app"].state, "session_factory", None)
        db = factory() if factory else Session(get_engine())
        scope["db"] = db
        language = locale.set("en")
        try:
            try:
                await authenticate(Request(scope))
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
                response.headers["Cache-Control"] = "no-store"
                return await response(scope, receive, send)

            async def send_response(message):
                if message["type"] == "http.response.start":
                    # Mutating handlers commit explicitly before returning. Roll back
                    # any remaining read or failed-handler transaction before streaming.
                    db.rollback()
                    message["headers"].extend([(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")])
                await send(message)

            await self.app(scope, receive, send_response)
        finally:
            db.rollback()
            rollback_staged_jobs(db)
            db.close()
            locale.reset(language)

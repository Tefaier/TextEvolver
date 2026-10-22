from starlette.requests import HTTPConnection

from text_evolver.db.models import UserAccount
from text_evolver.web.i18n import choose_language, locale
from text_evolver.web.sessions import state


class LocaleMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or scope.get("path", "").startswith("/static/"):
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        db = scope["db"]
        item = state(connection)
        user = db.get(UserAccount, item.user_id) if item.user_id else None
        scope["locale"] = choose_language(connection, user)
        # Release the account-preference read before handling the request.
        db.rollback()
        token = locale.set(scope["locale"])
        try:
            await self.app(scope, receive, send)
        finally:
            locale.reset(token)

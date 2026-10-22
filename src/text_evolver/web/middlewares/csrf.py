import hmac
from tempfile import SpooledTemporaryFile

from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from text_evolver.web.i18n import translate


class CSRFMiddleware:
    def __init__(self, app):
        self.app = app

    @staticmethod
    def matches(expected: str, supplied: object) -> bool:
        return isinstance(supplied, str) and hmac.compare_digest(expected.encode(), supplied.encode())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ["POST", "PATCH", "PUT", "DELETE"]:
            return await self.app(scope, receive, send)
        request = Request(scope)
        expected = request.session.get("csrf_token")
        rejected = JSONResponse({"detail": translate("Invalid CSRF token")}, status_code=403)
        if not isinstance(expected, str):
            return await rejected(scope, receive, send)
        supplied = request.headers.get("X-CSRF-Token")
        if supplied is not None:
            if not self.matches(expected, supplied):
                return await rejected(scope, receive, send)
            return await self.app(scope, receive, send)

        # The handler gets a new Request, so replay the body after checking its
        # form token. UploadFile provides asynchronous IO once the spool rolls
        # to disk, keeping large native form submissions out of memory.
        body = UploadFile(SpooledTemporaryFile(max_size=1024 * 1024), size=0)
        try:
            async def save_body():
                message = await receive()
                if message["type"] == "http.request":
                    await body.write(message.get("body", b""))
                return message

            try:
                async with Request(scope, save_body).form() as form:
                    supplied = form.get("csrf_token")
            except HTTPException as error:
                response = JSONResponse({"detail": translate(str(error.detail))}, status_code=error.status_code)
                return await response(scope, receive, send)
            if not self.matches(expected, supplied):
                return await rejected(scope, receive, send)

            await body.seek(0)
            remaining = body.size
            finished = False

            async def replay_body():
                nonlocal remaining, finished
                if finished:
                    return await receive()
                chunk = await body.read(64 * 1024)
                remaining -= len(chunk)
                finished = remaining == 0
                return {"type": "http.request", "body": chunk, "more_body": not finished}

            await self.app(scope, replay_body, send)
        finally:
            await body.close()

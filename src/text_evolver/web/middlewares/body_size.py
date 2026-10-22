from tempfile import SpooledTemporaryFile

from starlette.datastructures import Headers, UploadFile
from starlette.responses import JSONResponse

from text_evolver.web.i18n import translate

MAX_BODY_BYTES = 512 * 1024 * 1024


class BodySizeLimitMiddleware:
    def __init__(self, app, max_body_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        rejected = JSONResponse(
            {"detail": translate("Request body exceeds the 512 MB size limit")}, status_code=413,
        )
        for value in Headers(scope=scope).getlist("content-length"):
            try:
                declared_size = int(value)
            except ValueError:
                continue
            if declared_size > self.max_body_bytes:
                return await rejected(scope, receive, send)

        # Validate the complete body before handlers can change application state.
        # Large bodies spill to disk; UploadFile runs disk IO in a thread pool.
        body = UploadFile(SpooledTemporaryFile(max_size=1024 * 1024), size=0)
        try:
            received_size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                received_size += len(chunk)
                if received_size > self.max_body_bytes:
                    return await rejected(scope, receive, send)
                await body.write(chunk)
                if not message.get("more_body", False):
                    break

            await body.seek(0)
            remaining = received_size
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

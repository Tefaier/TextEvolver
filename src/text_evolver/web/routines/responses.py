from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from text_evolver.web.i18n import translate
from text_evolver.web.routines.flashes import flash
from text_evolver.web.routines.rendering import render


def redirect(request: Request, endpoint: str, **params: object) -> RedirectResponse:
    return RedirectResponse(request.url_for(endpoint, **params), status_code=status.HTTP_303_SEE_OTHER)


def accepts_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def validation_error_response(
    request: Request,
    message: str,
    template_name: str,
    *,
    status_code: int = status.HTTP_422_UNPROCESSABLE_CONTENT,
    **values: object,
) -> Response:
    if accepts_json(request):
        return JSONResponse(
            {"message": translate(message), "category": "error"},
            status_code=status_code,
        )
    flash(request, message)
    response = render(request, template_name, **values)
    response.status_code = status_code
    return response


def form_redirect(request: Request, endpoint: str, **params: object) -> Response:
    if accepts_json(request):
        return JSONResponse({"redirect": str(request.url_for(endpoint, **params))})
    return redirect(request, endpoint, **params)

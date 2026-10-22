from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from text_evolver.db.session import get_db
from text_evolver.web.auth import current_user
from text_evolver.web.routines.rendering import render
from text_evolver.web.routines.responses import redirect

router = APIRouter()


@router.get("/", response_class=HTMLResponse, name="index")
@router.get("/login", response_class=HTMLResponse, name="login_page")
def login_page(request: Request, session: Session = Depends(get_db)) -> Response:
    if current_user(request, session) is not None:
        return redirect(request, "my_settings")
    return render(request, "loging.html")

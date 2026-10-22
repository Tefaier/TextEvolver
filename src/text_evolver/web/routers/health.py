from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from text_evolver.db.session import get_db
from text_evolver.image_storage import ImageStorage, get_image_storage

router = APIRouter()


@router.get("/health/live", name="health_live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", name="health_ready")
def health_ready(
    session: Session = Depends(get_db),
    image_storage: ImageStorage = Depends(get_image_storage),
) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    image_storage.check_available()
    return {"status": "ready"}

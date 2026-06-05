import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import require_user
from app.models.recording import Recording

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/asr", tags=["ASR"])


@router.post("/{recording_id}/transcribe")
async def trigger_transcribe(recording_id: str, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    recording = db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.user_id == user["id"])
    ).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, u"\u5f55\u97f3\u4e0d\u5b58\u5728")

    if recording.status == "completed":
        return {"status": "completed", "message": u"\u5df2\u8f6c\u5199"}

    recording.status = "processing"
    db.commit()

    from app.tasks import process_recording
    asyncio.create_task(asyncio.to_thread(process_recording, recording_id))

    return {"status": "processing", "message": u"\u8f6c\u5199\u4e2d\u2026"}


@router.get("/{recording_id}/status")
async def get_status(recording_id: str, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    recording = db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.user_id == user["id"])
    ).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, u"\u5f55\u97f3\u4e0d\u5b58\u5728")
    return {"status": recording.status}
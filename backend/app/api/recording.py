import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
import uuid, os, io, random
from app.core.database import get_db
from app.core.storage import storage
from app.core.security import get_current_user, require_user, decode_access_token
from app.models.recording import Recording
from app.models.transcript import Transcript
from app.schemas import RecordingResponse, RecordingDetailResponse, TranscriptResponse

router = APIRouter(prefix="/api/recordings", tags=["Recordings"])

MIME_MAP = {
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
    "ogg": "audio/ogg", "flac": "audio/flac", "webm": "audio/webm",
}


@router.post("/upload", response_model=RecordingResponse)
async def upload_audio(
    file: UploadFile = File(...), title: str = Form(...), language: str = Form("zh"),
    db: Session = Depends(get_db), user: dict = Depends(require_user),
):
    ext = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    safe = f"{uuid.uuid4()}{ext}"
    content = await file.read()
    path = await storage.save_audio(content, safe)
    recording = Recording(
        user_id=user["id"], title=title, audio_path=path,
        audio_format=ext.lstrip("."), language=language, status="pending",
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return RecordingResponse(
        id=recording.id, title=recording.title, audio_duration=recording.audio_duration,
        audio_format=recording.audio_format, status=recording.status,
        language=recording.language, created_at=recording.created_at,
    )


@router.get("", response_model=list[RecordingResponse])
async def list_recordings(db: Session = Depends(get_db), user: dict = Depends(require_user)):
    recordings = db.execute(
        select(Recording).where(Recording.user_id == user["id"]).order_by(Recording.created_at.desc())
    ).scalars().all()
    return [
        RecordingResponse(
            id=r.id, title=r.title, audio_duration=r.audio_duration,
            audio_format=r.audio_format, status=r.status,
            language=r.language, created_at=r.created_at,
        ) for r in recordings
    ]


@router.get("/{recording_id}", response_model=RecordingDetailResponse)
async def get_recording(recording_id: str, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    recording = db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.user_id == user["id"])
    ).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "录音不存在")
    tlist = db.execute(
        select(Transcript)
        .where(Transcript.recording_id == recording_id)
        .order_by(Transcript.start_time)
    ).scalars().all()
    return RecordingDetailResponse(
        id=recording.id, title=recording.title,
        audio_duration=recording.audio_duration,
        audio_format=recording.audio_format, status=recording.status,
        language=recording.language, created_at=recording.created_at,
        transcripts=[
            TranscriptResponse(
                id=t.id, speaker=t.speaker, speaker_name=t.speaker_name,
                content=t.content, start_time=t.start_time,
                end_time=t.end_time, confidence=t.confidence,
            ) for t in tlist
        ],
    )


@router.get("/{recording_id}/audio")
async def stream_audio(recording_id: str, token: str = None, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """Stream audio -- accepts auth via header or ?token query param (for browser audio elements)"""
    user_id = user["id"] if user else None
    if not user_id and token:
        payload = decode_access_token(token)
        if payload:
            user_id = payload["sub"]
    if not user_id:
        raise HTTPException(401, "请先登录")

    recording = db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.user_id == user_id)
    ).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "录音不存在")
    path = os.path.abspath(recording.audio_path)
    if not os.path.exists(path):
        raise HTTPException(404, "音频文件不存在于磁盘")
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "wav"
    return FileResponse(path, media_type=MIME_MAP.get(ext, "audio/wav"))


@router.put("/transcripts/{transcript_id}")
async def update_transcript(
    transcript_id: str,
    content: str = Form(...),
    speaker_name: str = Form(""),
    db: Session = Depends(get_db), user: dict = Depends(require_user),
):
    t = db.execute(
        select(Transcript).where(Transcript.id == transcript_id)
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transcript not found")
    rec = db.execute(
        select(Recording).where(Recording.id == t.recording_id, Recording.user_id == user["id"])
    ).scalar_one_or_none()
    if not rec:
        raise HTTPException(403, "Access denied")
    t.content = content
    if speaker_name:
        t.speaker_name = speaker_name
    db.commit()
    return {"ok": True}


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    from celery.result import AsyncResult
    from app.core.celery_app import celery_app
    result = AsyncResult(task_id, app=celery_app)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }


@router.delete("/{recording_id}")
async def delete_recording(recording_id: str, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    recording = db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.user_id == user["id"])
    ).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "录音不存在")
    if os.path.exists(recording.audio_path):
        os.remove(recording.audio_path)
    db.delete(recording)
    db.commit()
    return {"ok": True}


@router.get("/{recording_id}/echo")
async def echo_recording(recording_id: str):
    return {"echo": recording_id}


@router.get("/{recording_id}/summary")
async def get_conversation_summary(recording_id: str, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    """Return pre-generated conversation summary. Falls back to on-demand generation if cache empty."""
    recording = db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.user_id == user["id"])
    ).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "录音不存在")
    if recording.summary_json:
        return json.loads(recording.summary_json)

    # Fallback: generate on demand for recordings that lack cached data
    tlist = db.execute(
        select(Transcript).where(Transcript.recording_id == recording_id).order_by(Transcript.start_time)
    ).scalars().all()
    if not tlist:
        return {"summary": "暂无转写内容", "topics": [], "key_points": []}

    from app.services.llm_service import llm_service
    transcript_data = [{"speaker": t.speaker, "speaker_name": t.speaker_name, "content": t.content} for t in tlist]
    try:
        result = await llm_service.summarize_conversation(transcript_data)
        recording.summary_json = json.dumps(result, ensure_ascii=False)
        db.commit()
        return result
    except Exception:
        return {"summary": "生成摘要失败，请稍后重试", "topics": [], "key_points": []}


@router.get("/{recording_id}/qa")
async def get_conversation_qa(recording_id: str, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    """Return pre-generated Q&A pairs. Falls back to on-demand generation if cache empty."""
    recording = db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.user_id == user["id"])
    ).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "录音不存在")
    if recording.qa_json:
        return json.loads(recording.qa_json)

    # Fallback: generate on demand for recordings that lack cached data
    tlist = db.execute(
        select(Transcript).where(Transcript.recording_id == recording_id).order_by(Transcript.start_time)
    ).scalars().all()
    if not tlist:
        return {"qa_pairs": []}

    from app.services.llm_service import llm_service
    transcript_data = [{"speaker": t.speaker, "speaker_name": t.speaker_name, "content": t.content} for t in tlist]
    try:
        result = await llm_service.extract_qa_pairs(transcript_data)
        recording.qa_json = json.dumps(result, ensure_ascii=False)
        db.commit()
        return result
    except Exception:
        return {"qa_pairs": []}
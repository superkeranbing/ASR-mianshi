"""Background processing - synchronous for thread pool execution"""
import json, logging, asyncio

logger = logging.getLogger(__name__)


def process_recording(recording_id: str):
    """Run ASR + summary + QA in background thread. Synchronous to avoid event loop blocking."""
    from app.core.database import SessionLocal
    from app.models.recording import Recording
    from app.models.transcript import Transcript

    logger.info(f"Starting transcription for {recording_id}")
    db = SessionLocal()
    try:
        recording = db.query(Recording).filter(Recording.id == recording_id).first()
        if not recording:
            logger.error(f"Recording {recording_id} not found")
            return

        recording.status = "processing"
        db.commit()

        from app.services.asr_engine import asr_engine
        segments = asyncio.run(asr_engine.transcribe(recording.audio_path))

        recording.audio_duration = segments[-1].end_time if segments else 0
        recording.status = "completed"
        for seg in segments:
            db.add(Transcript(
                recording_id=recording_id, speaker=seg.speaker, speaker_name=seg.speaker_name,
                content=seg.content, start_time=seg.start_time,
                end_time=seg.end_time, confidence=seg.confidence,
            ))
        db.commit()
        logger.info(f"Transcription done: {len(segments)} segments")

        if segments:
            from app.services.llm_service import llm_service
            tdata = [{"speaker":t.speaker,"speaker_name":t.speaker_name,"content":t.content}
                     for t in db.query(Transcript).filter(Transcript.recording_id==recording_id)
                     .order_by(Transcript.start_time).all()]
            try:
                recording.summary_json = json.dumps(
                    asyncio.run(llm_service.summarize_conversation(tdata)), ensure_ascii=False)
            except Exception as e:
                logger.error(f"Summary: {e}")
            try:
                recording.qa_json = json.dumps(
                    asyncio.run(llm_service.extract_qa_pairs(tdata)), ensure_ascii=False)
            except Exception as e:
                logger.error(f"QA: {e}")
            db.commit()

    except Exception as e:
        logger.error(f"Processing failed: {e}")
        try:
            rec = db.query(Recording).filter(Recording.id == recording_id).first()
            if rec: rec.status = "failed"; db.commit()
        except:
            pass
    finally:
        db.close()
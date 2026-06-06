"""Celery worker entry point

Usage:
  celery -A celery_worker worker -l info   # -P solo only needed on Windows; Docker uses default prefork
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.celery_app import celery_app
import app.tasks.asr_tasks          # noqa: register transcribe_audio_task
import app.tasks.interview_tasks    # noqa: register analyze_interview_task

if __name__ == "__main__":
    celery_app.start()

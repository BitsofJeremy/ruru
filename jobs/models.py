"""Job data classes for the in-process queue."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TTSJob:
    """A TTS generation job."""
    job_id: int
    text: str
    voice_id: int
    voice_type: str
    voice_description: str
    ref_audio_path: str
    ref_text: str
    audio_format: str


@dataclass
class NarrateJob:
    """A document narration job."""
    job_id: int
    chunks: list[str]
    voice_type: str
    voice_description: str
    ref_audio_path: str
    ref_text: str
    audio_format: str
    job_title: str
    chapter_boundaries: list[dict]
    author: str
    cover_path: str
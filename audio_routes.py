"""Audio upload, transcription, and voice analysis endpoints."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal

from av.error import FFmpegError
from fastapi import APIRouter, Form, HTTPException, UploadFile

from analysis_service import analyze_question
from dataset_store import get_dataset_path
from llm_client import LLMError
from speech_to_text import MAX_AUDIO_BYTES, transcribe_audio

router = APIRouter(tags=["Audio"])
logger = logging.getLogger(__name__)
_transcription_lock = Lock()
Language = Literal["auto", "en", "ar"]
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".aac", ".opus"}


def _transcribe_upload(file: UploadFile, language: str) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Upload a supported audio file, such as .m4a, .wav, or .mp3.",
        )

    content = file.file.read(MAX_AUDIO_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The audio file is empty.")
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be 10 MB or smaller.")

    with TemporaryDirectory(prefix="voice_analysis_") as directory:
        # Use a fixed basename rather than the filename supplied by the client.
        audio_path = Path(directory) / f"audio{suffix}"
        audio_path.write_bytes(content)
        try:
            # Serialize speech inference, including the initial model load.
            with _transcription_lock:
                return transcribe_audio(
                    audio_path,
                    language=None if language == "auto" else language,
                )
        except FFmpegError as exc:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file could not be decoded as audio.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Audio transcription failed")
            raise HTTPException(
                status_code=503,
                detail="Speech recognition failed. Check the API terminal for details.",
            ) from exc


@router.post("/transcribe")
def transcribe_endpoint(
    file: UploadFile,
    language: Language = Form("auto"),
):
    return _transcribe_upload(file, language)


@router.post("/analyze/audio")
def analyze_audio_endpoint(
    file: UploadFile,
    dataset_id: str = Form(..., pattern=r"^[0-9a-f]{32}$"),
    language: Language = Form("auto"),
):
    try:
        # Check the dataset before spending time on speech recognition.
        get_dataset_path(dataset_id)
        transcription = _transcribe_upload(file, language)
        analysis = analyze_question(dataset_id, transcription["text"])
        return {**analysis, "transcription": transcription}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

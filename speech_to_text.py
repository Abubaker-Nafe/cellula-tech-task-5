import argparse
import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from faster_whisper import WhisperModel

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_SECONDS = 60


@lru_cache(maxsize=1)
def get_whisper_model():
    model_dir = PROJECT_DIR / "data" / "models" / "whisper"
    model_dir.mkdir(parents=True, exist_ok=True)

    return WhisperModel(
        os.getenv("WHISPER_MODEL", "small").strip(),
        device="cpu",
        compute_type="int8",
        download_root=str(model_dir),
    )


def transcribe_audio(
    file_path: str | Path,
    language: str | None = None,
) -> dict:
    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        raise ValueError("Use a nonempty audio file up to 10 MB.")

    if language not in (None, "en", "ar"):
        raise ValueError(
            "Use en, ar, or automatic language detection."
        )

    segments, info = get_whisper_model().transcribe(
        str(path),
        language=language,
        task="transcribe",
        beam_size=5,
        vad_filter=True,
    )

    if info.duration > MAX_AUDIO_SECONDS:
        raise ValueError(
            "Use an audio recording of 60 seconds or less."
        )

    text = " ".join(
        segment.text.strip() for segment in segments
    ).strip()

    if not text:
        raise ValueError(
            "No speech was recognized. Try a clearer recording."
        )

    return {
        "status": "transcribed",
        "text": text,
        "language": info.language,
        "duration_seconds": round(float(info.duration), 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transcribe English or Arabic audio."
    )
    parser.add_argument("audio_file")
    parser.add_argument(
        "--language",
        choices=["auto", "en", "ar"],
        default="auto",
    )
    args = parser.parse_args()

    print(
        "Loading the speech model and transcribing...",
        flush=True,
    )

    try:
        result = transcribe_audio(
            args.audio_file,
            language=(
                None if args.language == "auto"
                else args.language
            ),
        )
    except Exception as exc:
        raise SystemExit(f"Transcription failed: {exc}")

    print(json.dumps(result, ensure_ascii=False, indent=2))
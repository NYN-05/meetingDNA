import asyncio
from functools import lru_cache

import whisper


@lru_cache(maxsize=1)
def _load_model(model_name: str = "base"):
    try:
        return whisper.load_model(model_name)
    except Exception as exc:
        raise RuntimeError(f"Failed to load Whisper model '{model_name}'.") from exc

class TranscriptionService:
    def __init__(self):
        self.model_name = "base"

    async def transcribe(self, audio_path: str) -> str:
        """Converts audio file to text transcript."""
        model = _load_model(self.model_name)
        result = await asyncio.to_thread(model.transcribe, audio_path)
        return result['text']

transcription_service = TranscriptionService()

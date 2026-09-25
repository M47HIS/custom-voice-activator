"""Pluggable local transcription engines for the Docker app."""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
from pathlib import Path


class TranscriptionError(RuntimeError):
    """Raised when the selected transcription engine cannot produce text."""


class Transcriber:
    """Lazy local transcriber.

    Engine priority:
    1. TRANSCRIBE_COMMAND, for user-provided local model runners.
    2. faster-whisper, installed in the backend Docker image.
    """

    def __init__(self) -> None:
        self.engine = os.getenv("ENGINE", "faster-whisper")
        self.command = os.getenv("TRANSCRIBE_COMMAND", "").strip()
        self.model_name = os.getenv("WHISPER_MODEL", "tiny.en")
        self.device = os.getenv("WHISPER_DEVICE", "cpu")
        self.compute_type = os.getenv("WHISPER_COMPUTE", "int8")
        self.language = os.getenv("LANGUAGE", "en")
        self._model = None
        self._lock = threading.Lock()

    def transcribe_file(self, path: Path) -> str:
        if self.command:
            return self._transcribe_with_command(path)
        return self._transcribe_with_faster_whisper(path)

    def _transcribe_with_command(self, path: Path) -> str:
        if "{file}" in self.command:
            command = self.command.replace("{file}", str(path))
            argv = shlex.split(command)
        else:
            argv = [*shlex.split(self.command), str(path)]

        try:
            result = subprocess.run(
                argv,
                check=True,
                capture_output=True,
                text=True,
                timeout=int(os.getenv("TRANSCRIBE_TIMEOUT", "300")),
            )
        except subprocess.CalledProcessError as exc:
            raise TranscriptionError(f"Transcription command failed (rc={exc.returncode}).") from exc
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError("Transcription command timed out.") from exc

        return result.stdout.strip()

    def _ensure_faster_whisper(self):
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(
                    "faster-whisper is not installed. Install it or set TRANSCRIBE_COMMAND."
                ) from exc

            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            return self._model

    def _transcribe_with_faster_whisper(self, path: Path) -> str:
        model = self._ensure_faster_whisper()
        segments, _info = model.transcribe(
            str(path),
            language=self.language or None,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

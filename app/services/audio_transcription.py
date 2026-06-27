from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from yt_dlp import YoutubeDL

from app.config import settings


class AudioTranscriptionUnavailable(RuntimeError):
    """Raised when audio download or faster-whisper transcription cannot run."""


@dataclass
class AudioTranscriptionResult:
    plain_text: str
    language_code: str
    segments: list[dict[str, Any]]


ProgressCallback = Callable[[str, int], None]


YOUTUBE_PLAYER_CLIENT_STRATEGIES = (
    ("android_vr",),
    ("android",),
    ("ios",),
    ("web",),
)


class FasterWhisperTranscriber:
    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        max_duration_seconds: int | None = None,
    ):
        self.model_name = model_name or settings.faster_whisper_model
        self.device = device or settings.faster_whisper_device
        self.compute_type = compute_type or settings.faster_whisper_compute_type
        self.max_duration_seconds = (
            max_duration_seconds
            if max_duration_seconds is not None
            else settings.audio_transcription_max_duration_seconds
        )

    def transcribe_video(
        self,
        video_url: str,
        progress_callback: ProgressCallback | None = None,
    ) -> AudioTranscriptionResult:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise AudioTranscriptionUnavailable(
                "faster-whisper is not installed. Run `pip install -r requirements.txt`."
            ) from exc

        with TemporaryDirectory(prefix="aveti-audio-") as temp_dir:
            self._notify(progress_callback, "Downloading source audio", 20)
            audio_path = self._download_audio(video_url, Path(temp_dir))
            self._notify(progress_callback, "Loading faster-whisper model", 45)
            model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._notify(progress_callback, "Transcribing audio", 60)
            segments_iter, info = model.transcribe(str(audio_path), vad_filter=True)
            duration = getattr(info, "duration_after_vad", None) or getattr(info, "duration", None)
            last_progress = 60
            segments = []
            text_parts = []
            for index, segment in enumerate(segments_iter):
                text = segment.text.strip()
                if not text:
                    continue
                if duration:
                    segment_progress = min(95, 60 + round((float(segment.end) / float(duration)) * 35))
                    if segment_progress > last_progress:
                        last_progress = segment_progress
                        self._notify(progress_callback, "Transcribing audio", segment_progress)
                text_parts.append(text)
                segments.append(
                    {
                        "segment_index": index,
                        "start_time_seconds": float(segment.start),
                        "end_time_seconds": float(segment.end),
                        "text": text,
                    }
                )

        plain_text = "\n".join(text_parts).strip()
        if not plain_text:
            raise AudioTranscriptionUnavailable("faster-whisper returned an empty transcript.")

        return AudioTranscriptionResult(
            plain_text=plain_text,
            language_code=getattr(info, "language", None) or "unknown",
            segments=segments,
        )

    @staticmethod
    def _notify(
        progress_callback: ProgressCallback | None,
        step: str,
        progress_percent: int,
    ) -> None:
        if progress_callback is not None:
            progress_callback(step, progress_percent)

    def _download_audio(self, video_url: str, temp_dir: Path) -> Path:
        probe_options = self._yt_dlp_options()
        try:
            with YoutubeDL(probe_options) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except Exception as exc:
            raise AudioTranscriptionUnavailable(f"Unable to inspect source audio: {exc}") from exc

        duration = info.get("duration")
        if (
            self.max_duration_seconds
            and duration
            and int(duration) > self.max_duration_seconds
        ):
            raise AudioTranscriptionUnavailable(
                f"Video is longer than the configured {self.max_duration_seconds} second limit."
            )

        errors = []
        for strategy_index, player_clients in enumerate(YOUTUBE_PLAYER_CLIENT_STRATEGIES, 1):
            download_options = self._yt_dlp_options(
                outtmpl=str(temp_dir / f"audio-{strategy_index}.%(ext)s"),
                player_clients=player_clients,
            )
            try:
                with YoutubeDL(download_options) as ydl:
                    ydl.extract_info(video_url, download=True)
            except Exception as exc:
                errors.append(f"{'/'.join(player_clients)}: {exc}")
                continue

            audio_files = [
                path
                for path in temp_dir.iterdir()
                if path.is_file() and path.suffix != ".part"
            ]
            if audio_files:
                return max(audio_files, key=lambda path: path.stat().st_mtime)

        error_text = "; ".join(errors) if errors else "no audio file was created"
        hint = ""
        if "403" in error_text or "Forbidden" in error_text:
            hint = " Update yt-dlp and retry; YouTube may have blocked an older extractor route."
        raise AudioTranscriptionUnavailable(
            f"Unable to download source audio: {error_text}.{hint}"
        )

    def _yt_dlp_options(
        self,
        outtmpl: str | None = None,
        player_clients: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "noplaylist": True,
            "proxy": "",
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        if outtmpl is not None:
            options["outtmpl"] = outtmpl
        if player_clients is not None:
            options["extractor_args"] = {
                "youtube": {
                    "player_client": list(player_clients),
                }
            }
        return options

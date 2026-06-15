from __future__ import annotations

from dataclasses import dataclass

from youtube_transcript_api import YouTubeTranscriptApi


class YouTubeCaptionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class YouTubeCaptionResult:
    plain_text: str
    segments: list[dict]
    language_code: str


class YouTubeCaptionClient:
    def __init__(self, languages: list[str]):
        self.languages = languages
        self.api = YouTubeTranscriptApi()

    def fetch(self, video_id: str) -> YouTubeCaptionResult:
        try:
            transcript = self.api.fetch(video_id, languages=self.languages)
        except Exception as exc:
            raise YouTubeCaptionUnavailable(str(exc)) from exc

        segments = []
        texts = []
        for index, snippet in enumerate(transcript):
            text = " ".join(snippet.text.split())
            if not text:
                continue
            start = float(snippet.start)
            duration = float(snippet.duration)
            texts.append(text)
            segments.append(
                {
                    "segment_index": index,
                    "start_time_seconds": start,
                    "end_time_seconds": start + duration,
                    "text": text,
                }
            )

        if not texts:
            raise YouTubeCaptionUnavailable("YouTube returned an empty caption track.")

        return YouTubeCaptionResult(
            plain_text=" ".join(texts),
            segments=segments,
            language_code=transcript.language_code,
        )


from __future__ import annotations

import json
from dataclasses import dataclass
from html import unescape
from urllib.request import urlopen

from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL


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
        errors = []
        try:
            transcript = self.api.fetch(video_id, languages=self.languages)
            return self._format_transcript(transcript)
        except Exception as exc:
            errors.append(str(exc))

        try:
            transcript_list = self.api.list(video_id)
        except Exception as exc:
            errors.append(str(exc))
        else:
            for available_transcript in transcript_list:
                try:
                    transcript = available_transcript.fetch()
                    return self._format_transcript(transcript)
                except Exception as exc:
                    errors.append(str(exc))

        try:
            return self._fetch_with_ytdlp(video_id)
        except Exception as exc:
            errors.append(str(exc))
            raise YouTubeCaptionUnavailable("; ".join(errors)) from exc

    @staticmethod
    def _format_transcript(transcript) -> YouTubeCaptionResult:
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

    def _fetch_with_ytdlp(self, video_id: str) -> YouTubeCaptionResult:
        url = f"https://www.youtube.com/watch?v={video_id}"
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        tracks_by_language = {
            **(info.get("automatic_captions") or {}),
            **(info.get("subtitles") or {}),
        }
        language_codes = [
            language
            for language in self.languages
            if language in tracks_by_language
        ]
        language_codes.extend(
            language
            for language in tracks_by_language
            if language not in language_codes
        )

        for language_code in language_codes:
            tracks = tracks_by_language.get(language_code) or []
            track = next((item for item in tracks if item.get("ext") == "json3"), None)
            if track is None:
                continue
            result = self._fetch_json3_track(track["url"], language_code)
            if result.plain_text:
                return result

        raise YouTubeCaptionUnavailable("yt-dlp did not find a readable transcript track.")

    @staticmethod
    def _fetch_json3_track(url: str, language_code: str) -> YouTubeCaptionResult:
        with urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        segments = []
        texts = []
        for event in payload.get("events") or []:
            raw_text = "".join(
                segment.get("utf8", "")
                for segment in event.get("segs") or []
            )
            text = " ".join(unescape(raw_text).split())
            if not text:
                continue
            start = float(event.get("tStartMs", 0)) / 1000
            duration = float(event.get("dDurationMs", 0)) / 1000
            texts.append(text)
            segments.append(
                {
                    "segment_index": len(segments),
                    "start_time_seconds": start,
                    "end_time_seconds": start + duration if duration else None,
                    "text": text,
                }
            )

        if not texts:
            raise YouTubeCaptionUnavailable("yt-dlp returned an empty transcript track.")

        return YouTubeCaptionResult(
            plain_text=" ".join(texts),
            segments=segments,
            language_code=language_code,
        )

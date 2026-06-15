from dataclasses import asdict
from typing import Any

from scrape_khan_grammar import (
    VideoCandidate,
    content_route_payload,
    fetch_content as scraper_fetch_content,
    fetch_course as scraper_fetch_course,
    full_url,
    iter_video_candidates,
    transcript_from_content,
)


class KhanClient:
    def __init__(self, country_code: str = "US", cookie: str | None = None):
        self.country_code = country_code
        self.cookie = cookie

    def fetch_course(self, path: str) -> dict[str, Any]:
        return scraper_fetch_course(path, self.country_code, self.cookie)

    def fetch_content(self, path: str) -> dict[str, Any]:
        return scraper_fetch_content(path, self.country_code, self.cookie)

    @staticmethod
    def course_payload(response: dict[str, Any]) -> dict[str, Any]:
        course = content_route_payload(response).get("course")
        if not course:
            raise RuntimeError(
                "This URL does not appear to be a supported Khan Academy course page."
            )
        return course

    @staticmethod
    def video_candidates(response: dict[str, Any]) -> list[VideoCandidate]:
        return iter_video_candidates(response)

    @staticmethod
    def transcript(content: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        text = transcript_from_content(content)
        segments = []
        for index, subtitle in enumerate(content.get("subtitles") or []):
            segment_text = " ".join((subtitle.get("text") or "").split())
            if not segment_text:
                continue
            segments.append(
                {
                    "segment_index": index,
                    "start_time_seconds": KhanClient.subtitle_time_seconds(
                        subtitle.get("startTime")
                    ),
                    "end_time_seconds": KhanClient.subtitle_time_seconds(
                        subtitle.get("endTime")
                    ),
                    "text": segment_text,
                }
            )
        return text, segments

    @staticmethod
    def subtitle_time_seconds(value: Any) -> float | None:
        if value is None:
            return None
        return float(value) / 1000

    @staticmethod
    def candidate_dict(candidate: VideoCandidate) -> dict[str, Any]:
        return asdict(candidate)


__all__ = ["KhanClient", "VideoCandidate", "full_url"]

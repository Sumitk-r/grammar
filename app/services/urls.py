from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class InvalidCourseUrl(ValueError):
    pass


@dataclass(frozen=True)
class SourceUrl:
    submitted_url: str
    normalized_path: str
    source_type: str


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}


def validate_course_url(value: str) -> SourceUrl:
    value = value.strip()
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise InvalidCourseUrl(
            "Please enter a valid Khan Academy course or YouTube playlist URL."
        ) from exc

    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        raise InvalidCourseUrl(
            "Please enter a valid Khan Academy course or YouTube playlist URL."
        )

    if hostname in YOUTUBE_HOSTS:
        query = parse_qs(parsed.query)
        playlist_id = (query.get("list") or [""])[0].strip()
        if not playlist_id:
            raise InvalidCourseUrl("Please enter a valid YouTube playlist URL.")
        return SourceUrl(
            submitted_url=value,
            normalized_path=f"youtube_playlist:{playlist_id}",
            source_type="youtube_playlist",
        )

    if not (hostname == "khanacademy.org" or hostname.endswith(".khanacademy.org")):
        raise InvalidCourseUrl(
            "Please enter a valid Khan Academy course or YouTube playlist URL."
        )

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] not in {"humanities", "math", "science", "computing", "economics-finance-domain", "test-prep"}:
        raise InvalidCourseUrl(
            "This URL does not appear to be a supported Khan Academy course page."
        )

    path = "/" + "/".join(parts)
    return SourceUrl(
        submitted_url=value,
        normalized_path=path.rstrip("/"),
        source_type="khan_course",
    )


def is_youtube_playlist_path(value: str) -> bool:
    return value.startswith("youtube_playlist:")


def youtube_playlist_id_from_path(value: str) -> str:
    return value.split(":", 1)[1]

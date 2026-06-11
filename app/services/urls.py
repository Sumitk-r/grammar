from dataclasses import dataclass
from urllib.parse import urlparse


class InvalidCourseUrl(ValueError):
    pass


@dataclass(frozen=True)
class CourseUrl:
    submitted_url: str
    normalized_path: str


def validate_course_url(value: str) -> CourseUrl:
    value = value.strip()
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise InvalidCourseUrl("Please enter a valid Khan Academy course URL.") from exc

    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        hostname == "khanacademy.org" or hostname.endswith(".khanacademy.org")
    ):
        raise InvalidCourseUrl("Please enter a valid Khan Academy course URL.")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] not in {"humanities", "math", "science", "computing", "economics-finance-domain", "test-prep"}:
        raise InvalidCourseUrl(
            "This URL does not appear to be a supported Khan Academy course page."
        )

    path = "/" + "/".join(parts)
    return CourseUrl(
        submitted_url=value,
        normalized_path=path.rstrip("/"),
    )


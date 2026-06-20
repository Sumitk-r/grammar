import pytest

from app.services.urls import InvalidCourseUrl, validate_course_url


def test_valid_course_url_is_normalized():
    result = validate_course_url(
        "https://www.khanacademy.org/humanities/grammar/?ref=example"
    )
    assert result.normalized_path == "/humanities/grammar"
    assert result.source_type == "khan_course"


def test_valid_youtube_playlist_url_is_detected():
    result = validate_course_url(
        "https://www.youtube.com/playlist?list=PL1234567890&si=example"
    )
    assert result.normalized_path == "youtube_playlist:PL1234567890"
    assert result.source_type == "youtube_playlist"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/humanities/grammar",
        "javascript:alert(1)",
        "https://www.khanacademy.org/",
        "https://www.khanacademy.org/profile/me",
    ],
)
def test_invalid_or_unsupported_url_is_rejected(url):
    with pytest.raises(InvalidCourseUrl):
        validate_course_url(url)

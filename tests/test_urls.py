import pytest

from app.services.urls import InvalidCourseUrl, validate_course_url


def test_valid_course_url_is_normalized():
    result = validate_course_url(
        "https://www.khanacademy.org/humanities/grammar/?ref=example"
    )
    assert result.normalized_path == "/humanities/grammar"


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


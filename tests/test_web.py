from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Course, Lesson, Unit, Video
from app.routes.web import local_datetime


def test_local_datetime_renders_ist_label():
    rendered = local_datetime(datetime(2026, 6, 17, 9, 57, tzinfo=timezone.utc))

    assert rendered == "Jun 17, 2026 15:27 IST"


def test_home_page_includes_global_search(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Search every transcript" in response.text
    assert 'id="global-search"' in response.text


def test_video_page_explains_missing_khan_transcript_without_youtube_id(client):
    with SessionLocal() as db:
        course = Course(
            title="Khan Course",
            slug="khan-course",
            relative_url="/science/course",
            source_url="https://www.khanacademy.org/science/course",
        )
        unit = Unit(course=course, unit_index=1, title="Unit")
        lesson = Lesson(unit=unit, lesson_index=1, title="Lesson")
        video = Video(
            lesson=lesson,
            video_index=1,
            title="Khan only video",
            relative_url="/science/course/unit/lesson/v/khan-only",
            full_url="https://www.khanacademy.org/science/course/unit/lesson/v/khan-only",
            youtube_id=None,
            scrape_status="no_transcript",
        )
        db.add(course)
        db.commit()
        video_id = video.id

    response = client.get(f"/videos/{video_id}")

    assert response.status_code == 200
    assert (
        "No transcript is available because Khan Academy did not provide subtitles "
        "for this lecture, and no YouTube video ID was found."
    ) in response.text
    assert 'id="generate-transcript-form"' not in response.text

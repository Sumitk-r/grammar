from app.config import settings
from app.database import SessionLocal
from app.models import Course, Lesson, ScrapeJob, Unit, Video
from app.services.job_processor import audio_transcript_path
from sqlalchemy import select


def create_category(client, name="Grammar"):
    response = client.post(
        "/api/categories",
        json={"name": name},
        headers={"X-Admin-Key": settings.admin_key},
    )
    assert response.status_code == 201
    return response.json()


def test_admin_can_create_category(client):
    category = create_category(client, "Science")

    assert category["name"] == "Science"
    assert category["slug"] == "science"

    response = client.get("/api/categories")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Science"


def test_non_admin_cannot_create_category(client):
    response = client.post("/api/categories", json={"name": "Science"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Admin key is required."

    response = client.post(
        "/api/categories",
        json={"name": "Science"},
        headers={"X-Admin-Key": "wrong"},
    )
    assert response.status_code == 401


def test_create_job_and_reuse_active_duplicate(client):
    category = create_category(client)
    payload = {
        "url": "https://www.khanacademy.org/humanities/grammar",
        "category_id": category["id"],
    }
    first = client.post("/api/jobs", json=payload)
    second = client.post("/api/jobs", json=payload)

    assert first.status_code == 201
    assert first.json()["status"] == "queued"
    assert second.status_code == 201
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["reused"] is True


def test_create_job_without_category(client):
    response = client.post(
        "/api/jobs",
        json={"url": "https://www.khanacademy.org/humanities/grammar"},
    )

    assert response.status_code == 201
    with SessionLocal() as db:
        job = db.get(ScrapeJob, response.json()["job_id"])
        assert job.category_id is None


def test_rejects_unsupported_source_url(client):
    category = create_category(client)
    response = client.post(
        "/api/jobs",
        json={
            "url": "https://example.com/humanities/grammar",
            "category_id": category["id"],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Please enter a valid Khan Academy course or YouTube playlist URL."


def test_rejects_job_with_unknown_category(client):
    response = client.post(
        "/api/jobs",
        json={
            "url": "https://www.khanacademy.org/humanities/grammar",
            "category_id": "missing",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Category not found."


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_admin_can_queue_audio_transcript_generation(client):
    with SessionLocal() as db:
        course = Course(
            title="Playlist",
            slug="playlist",
            relative_url="youtube_playlist:PL123",
            source_url="https://www.youtube.com/playlist?list=PL123",
        )
        unit = Unit(course=course, unit_index=1, title="YouTube playlist")
        lesson = Lesson(unit=unit, lesson_index=1, title="Videos")
        video = Video(
            lesson=lesson,
            video_index=1,
            title="Missing transcript video",
            relative_url="youtube:playlist:PL123:video:abc123",
            full_url="https://www.youtube.com/watch?v=abc123",
            youtube_id="abc123",
            content_kind="YouTubeVideo",
            scrape_status="no_transcript",
        )
        db.add(course)
        db.commit()
        video_id = video.id

    response = client.post(
        f"/api/videos/{video_id}/generate-transcript",
        headers={"X-Admin-Key": settings.admin_key},
    )

    assert response.status_code == 202
    assert response.json()["message"] == "Transcript generation queued."
    with SessionLocal() as db:
        job = db.scalar(select(ScrapeJob))
        video = db.get(Video, video_id)
        assert job.normalized_path == audio_transcript_path(video_id)
        assert job.submitted_url == "https://www.youtube.com/watch?v=abc123"
        assert video.scrape_status == "queued_transcription"


def test_youtube_backfill_requires_admin_key(client):
    with SessionLocal() as db:
        course = Course(
            title="Playlist",
            slug="playlist",
            relative_url="youtube_playlist:PL123",
            source_url="https://www.youtube.com/playlist?list=PL123",
        )
        db.add(course)
        db.commit()
        course_id = course.id

    response = client.post(f"/api/courses/{course_id}/youtube-backfill")

    assert response.status_code == 401

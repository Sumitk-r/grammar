from pathlib import Path

from sqlalchemy import func, select

from app.database import SessionLocal
from app.importer import import_transcript_csv
from app.models import Course, Lesson, Transcript, Unit, Video


def test_importer_is_idempotent():
    path = Path(__file__).parents[1] / "sample_transcripts.csv"
    with SessionLocal() as db:
        course, first_count = import_transcript_csv(db, path)
        _, second_count = import_transcript_csv(db, path)
        course_count = db.scalar(select(func.count(Course.id)))
        video_count = db.scalar(select(func.count(Video.id)))
        transcript_count = db.scalar(select(func.count(Transcript.id)))

    assert course.title == "Grammar"
    assert first_count == 1
    assert second_count == 1
    assert course_count == 1
    assert video_count == 1
    assert transcript_count == 1


def test_imported_course_is_browsable_and_exportable(client):
    path = Path(__file__).parents[1] / "sample_transcripts.csv"
    with SessionLocal() as db:
        course, _ = import_transcript_csv(db, path)
        course_id = course.id
        video_id = db.scalar(select(Video.id))

    page = client.get(f"/courses/{course_id}")
    search = client.get(
        "/api/search",
        params={"course_id": course_id, "q": "grammar"},
    )
    transcript = client.get(f"/api/videos/{video_id}/transcript")
    csv_export = client.get(f"/api/courses/{course_id}/export.csv")
    json_export = client.get(f"/api/courses/{course_id}/export.json")

    assert page.status_code == 200
    assert "Introduction to grammar" in page.text
    assert "Imported" in page.text
    assert search.status_code == 200
    assert search.json()[0]["video_title"] == "Introduction to grammar"
    assert transcript.status_code == 200
    assert transcript.json()["plain_text"].startswith("- [Voiceover]")
    assert transcript.json()["source"] == "csv_import"
    assert csv_export.status_code == 200
    assert "text/csv" in csv_export.headers["content-type"]
    assert json_export.status_code == 200
    assert json_export.json()["course"]["title"] == "Grammar"


def test_course_can_be_deleted_from_api(client):
    path = Path(__file__).parents[1] / "sample_transcripts.csv"
    with SessionLocal() as db:
        course, _ = import_transcript_csv(db, path)
        course_id = course.id

    response = client.delete(f"/api/courses/{course_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Course.id))) == 0
        assert db.scalar(select(func.count(Unit.id))) == 0
        assert db.scalar(select(func.count(Lesson.id))) == 0
        assert db.scalar(select(func.count(Video.id))) == 0
        assert db.scalar(select(func.count(Transcript.id))) == 0

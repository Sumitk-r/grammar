from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import (
    Course,
    JobStatus,
    ScrapeJob,
    Transcript,
    TranscriptSegment,
    Unit,
    Video,
)
from app.services.job_processor import process_job
from app.services.khan_client import VideoCandidate
from app.services.youtube_client import YouTubeCaptionResult
from app.services.youtube_backfill import backfill_missing_youtube_captions


class FakeKhanClient:
    def fetch_course(self, path):
        assert path == "/humanities/grammar"
        return {"response": "course"}

    def course_payload(self, response):
        assert response == {"response": "course"}
        return {
            "id": "course-1",
            "translatedTitle": "Grammar",
            "slug": "grammar",
            "relativeUrl": "/humanities/grammar",
            "translatedDescription": "Learn grammar.",
            "unitChildren": [
                {
                    "id": "unit-1",
                    "translatedTitle": "Nouns",
                    "slug": "nouns",
                    "relativeUrl": "/humanities/grammar/nouns",
                    "allOrderedChildren": [
                        {
                            "__typename": "Lesson",
                            "id": "lesson-1",
                            "translatedTitle": "Introduction",
                            "slug": "introduction",
                            "relativeUrl": "/humanities/grammar/nouns/introduction",
                        }
                    ],
                }
            ],
        }

    def video_candidates(self, response):
        return [
            VideoCandidate(
                unit_index=1,
                unit_title="Nouns",
                lesson_index=1,
                lesson_title="Introduction",
                video_index=1,
                title="Introduction to nouns",
                path="/humanities/grammar/nouns/introduction/v/intro-to-nouns",
                content_kind="Video",
            )
        ]

    def fetch_content(self, path):
        return {
            "id": "video-1",
            "translatedTitle": "Introduction to nouns",
            "contentKind": "Video",
            "youtubeId": "abc123",
            "duration": 30,
        }

    def transcript(self, content):
        return (
            "A noun names a person, place, thing, or idea.",
            [
                {
                    "segment_index": 0,
                    "start_time_seconds": 0.0,
                    "end_time_seconds": 4.0,
                    "text": "A noun names a person, place, thing, or idea.",
                }
            ],
        )


class FakeKhanClientWithoutTranscript(FakeKhanClient):
    def transcript(self, content):
        return "", []


class FakeYouTubeClient:
    def fetch(self, video_id):
        assert video_id == "abc123"
        return YouTubeCaptionResult(
            plain_text="A caption supplied by YouTube.",
            language_code="en",
            segments=[
                {
                    "segment_index": 0,
                    "start_time_seconds": 1.5,
                    "end_time_seconds": 3.75,
                    "text": "A caption supplied by YouTube.",
                }
            ],
        )


def test_khan_subtitle_times_are_converted_from_milliseconds():
    from app.services.khan_client import KhanClient

    text, segments = KhanClient.transcript(
        {
            "subtitles": [
                {
                    "startTime": 2153,
                    "endTime": 4220,
                    "text": "A short subtitle.",
                }
            ]
        }
    )

    assert text == "A short subtitle."
    assert segments[0]["start_time_seconds"] == 2.153
    assert segments[0]["end_time_seconds"] == 4.22


def test_worker_processes_a_job_end_to_end():
    with SessionLocal() as db:
        job = ScrapeJob(
            submitted_url="https://www.khanacademy.org/humanities/grammar",
            normalized_path="/humanities/grammar",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    process_job(job_id, client=FakeKhanClient())

    with SessionLocal() as db:
        job = db.get(ScrapeJob, job_id)
        assert job.status == JobStatus.completed
        assert job.progress_percent == 100
        assert job.total_videos == 1
        assert job.processed_videos == 1
        assert db.scalar(select(func.count(Course.id))) == 1
        assert db.scalar(select(func.count(Unit.id))) == 1
        assert db.scalar(select(func.count(Video.id))) == 1
        assert db.scalar(select(func.count(Transcript.id))) == 1
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 1


def test_worker_uses_youtube_when_khan_has_no_transcript():
    with SessionLocal() as db:
        job = ScrapeJob(
            submitted_url="https://www.khanacademy.org/humanities/grammar",
            normalized_path="/humanities/grammar",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    process_job(
        job_id,
        client=FakeKhanClientWithoutTranscript(),
        youtube_client=FakeYouTubeClient(),
    )

    with SessionLocal() as db:
        video = db.scalar(select(Video))
        assert video.scrape_status == "completed"
        assert video.transcript.plain_text == "A caption supplied by YouTube."
        assert video.transcript.source == "youtube_captions"
        assert video.transcript.segments[0].start_time_seconds == 1.5


def test_youtube_backfill_updates_existing_missing_video():
    with SessionLocal() as db:
        course = Course(
            title="Course",
            slug="course",
            relative_url="/math/course",
            source_url="https://www.khanacademy.org/math/course",
        )
        unit = Unit(course=course, unit_index=1, title="Unit")
        from app.models import Lesson

        lesson = Lesson(unit=unit, lesson_index=1, title="Lesson")
        video = Video(
            lesson=lesson,
            video_index=1,
            title="Video",
            relative_url="/math/course/unit/lesson/v/video",
            full_url="https://www.khanacademy.org/math/course/unit/lesson/v/video",
            youtube_id="abc123",
            content_kind="Video",
            scrape_status="no_transcript",
        )
        db.add(course)
        db.commit()
        course_id = course.id

    result = backfill_missing_youtube_captions(
        course_id,
        client=FakeYouTubeClient(),
    )

    assert result["fetched"] == 1
    with SessionLocal() as db:
        video = db.scalar(select(Video))
        assert video.scrape_status == "completed"
        assert video.transcript.source == "youtube_captions"

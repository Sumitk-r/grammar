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


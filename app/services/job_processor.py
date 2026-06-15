from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Course,
    JobEvent,
    JobStatus,
    Lesson,
    ScrapeJob,
    Transcript,
    TranscriptSegment,
    Unit,
    Video,
)
from app.services.khan_client import KhanClient, VideoCandidate, full_url
from app.services.youtube_client import (
    YouTubeCaptionClient,
    YouTubeCaptionUnavailable,
)

logger = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(timezone.utc)


def add_event(
    db: Session,
    job: ScrapeJob,
    message: str,
    level: str = "info",
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        JobEvent(
            job_id=job.id,
            level=level,
            message=message,
            event_metadata=metadata,
        )
    )


def _upsert_course(db: Session, payload: dict[str, Any], path: str) -> Course:
    relative_url = payload.get("relativeUrl") or path
    course = db.scalar(select(Course).where(Course.relative_url == relative_url))
    if course is None:
        course = Course(relative_url=relative_url, source_url=full_url(relative_url))
        db.add(course)
    course.khan_course_id = payload.get("id")
    course.title = payload.get("translatedTitle") or payload.get("slug") or "Khan Academy course"
    course.slug = payload.get("slug") or relative_url.rstrip("/").split("/")[-1]
    course.description = payload.get("translatedDescription")
    return course


def _upsert_structure(
    db: Session,
    course: Course,
    course_payload: dict[str, Any],
) -> dict[tuple[int, int], Lesson]:
    lesson_map: dict[tuple[int, int], Lesson] = {}
    for unit_index, unit_payload in enumerate(course_payload.get("unitChildren") or [], 1):
        unit = db.scalar(
            select(Unit).where(
                Unit.course_id == course.id,
                Unit.unit_index == unit_index,
            )
        )
        if unit is None:
            unit = Unit(course=course, unit_index=unit_index, title="")
            db.add(unit)
        unit.khan_unit_id = unit_payload.get("id")
        unit.title = unit_payload.get("translatedTitle") or unit_payload.get("slug") or f"Unit {unit_index}"
        unit.slug = unit_payload.get("slug")
        unit.relative_url = unit_payload.get("relativeUrl")
        db.flush()

        lesson_index = 0
        for lesson_payload in unit_payload.get("allOrderedChildren") or []:
            if lesson_payload.get("__typename") != "Lesson":
                continue
            lesson_index += 1
            lesson = db.scalar(
                select(Lesson).where(
                    Lesson.unit_id == unit.id,
                    Lesson.lesson_index == lesson_index,
                )
            )
            if lesson is None:
                lesson = Lesson(
                    unit=unit,
                    lesson_index=lesson_index,
                    title="",
                )
                db.add(lesson)
            lesson.khan_lesson_id = lesson_payload.get("id")
            lesson.title = (
                lesson_payload.get("translatedTitle")
                or lesson_payload.get("slug")
                or f"Lesson {lesson_index}"
            )
            lesson.slug = lesson_payload.get("slug")
            lesson.relative_url = lesson_payload.get("relativeUrl")
            db.flush()
            lesson_map[(unit_index, lesson_index)] = lesson
    return lesson_map


def _upsert_video(
    db: Session,
    lesson: Lesson,
    candidate: VideoCandidate,
    content: dict[str, Any] | None = None,
) -> Video:
    video = db.scalar(select(Video).where(Video.relative_url == candidate.path))
    if video is None:
        video = Video(
            lesson=lesson,
            video_index=candidate.video_index,
            title=candidate.title,
            relative_url=candidate.path,
            full_url=full_url(candidate.path),
            content_kind=candidate.content_kind,
        )
        db.add(video)
    else:
        video.lesson = lesson
        video.video_index = candidate.video_index

    if content:
        video.khan_video_id = content.get("id")
        video.title = content.get("translatedTitle") or candidate.title
        video.readable_id = content.get("readableId") or content.get("slug")
        video.youtube_id = content.get("youtubeId") or content.get("translatedYoutubeId")
        duration = content.get("duration")
        video.duration_seconds = int(duration) if duration not in (None, "") else None
        video.content_kind = content.get("contentKind") or candidate.content_kind
    return video


def _replace_transcript(
    db: Session,
    video: Video,
    plain_text: str,
    segments: list[dict[str, Any]],
    source: str = "khan_subtitles",
    language_code: str = "en",
) -> None:
    transcript = video.transcript
    if transcript is None:
        transcript = Transcript(video=video, plain_text=plain_text)
        db.add(transcript)
    else:
        transcript.plain_text = plain_text
        transcript.segments.clear()
    transcript.source = source
    transcript.language_code = language_code
    db.flush()
    for segment in segments:
        transcript.segments.append(TranscriptSegment(**segment))


def process_job(
    job_id: str,
    client: KhanClient | None = None,
    youtube_client: YouTubeCaptionClient | None = None,
) -> None:
    client = client or KhanClient(settings.khan_country_code, settings.khan_cookie)
    if youtube_client is None and settings.youtube_fallback_enabled:
        languages = [
            language.strip()
            for language in settings.youtube_languages.split(",")
            if language.strip()
        ]
        youtube_client = YouTubeCaptionClient(languages)
    db = SessionLocal()
    try:
        job = db.get(ScrapeJob, job_id)
        if job is None or job.status != JobStatus.queued:
            return
        job.status = JobStatus.running
        job.started_at = now()
        job.current_step = "Fetching course structure"
        add_event(db, job, "Job started")
        db.commit()

        response = client.fetch_course(job.normalized_path)
        payload = client.course_payload(response)
        candidates = client.video_candidates(response)
        if settings.max_videos_per_job is not None:
            candidates = candidates[: settings.max_videos_per_job]

        job = db.get(ScrapeJob, job_id)
        course = _upsert_course(db, payload, job.normalized_path)
        db.flush()
        lesson_map = _upsert_structure(db, course, payload)
        job.course = course
        job.total_videos = len(candidates)
        job.current_step = "Processing videos"
        add_event(db, job, f"Found {len(candidates)} videos")
        db.commit()

        for index, candidate in enumerate(candidates, 1):
            job = db.get(ScrapeJob, job_id)
            if job.status == JobStatus.cancelled:
                job.current_step = "Cancelled"
                job.finished_at = now()
                add_event(db, job, "Job cancelled", "warning")
                db.commit()
                return

            lesson = lesson_map.get((candidate.unit_index, candidate.lesson_index))
            if lesson is None:
                job.failed_videos += 1
                add_event(
                    db,
                    job,
                    f"Missing lesson for {candidate.title}",
                    "error",
                )
                db.commit()
                continue

            try:
                content = client.fetch_content(candidate.path)
                video = _upsert_video(db, lesson, candidate, content)
                transcript_text, segments = client.transcript(content)
                if transcript_text:
                    _replace_transcript(db, video, transcript_text, segments)
                    video.scrape_status = "completed"
                    video.scrape_error = None
                elif video.youtube_id and youtube_client is not None:
                    try:
                        youtube_transcript = youtube_client.fetch(video.youtube_id)
                        _replace_transcript(
                            db,
                            video,
                            youtube_transcript.plain_text,
                            youtube_transcript.segments,
                            source="youtube_captions",
                            language_code=youtube_transcript.language_code,
                        )
                        video.scrape_status = "completed"
                        video.scrape_error = None
                        add_event(
                            db,
                            job,
                            f"Used YouTube captions for {candidate.title}",
                        )
                    except YouTubeCaptionUnavailable as exc:
                        video.scrape_status = "no_transcript"
                        video.scrape_error = f"YouTube captions unavailable: {exc}"
                else:
                    video.scrape_status = "no_transcript"
                    video.scrape_error = None
                job.processed_videos += 1
                add_event(db, job, f"Processed {candidate.title}")
            except Exception as exc:
                logger.exception("Video processing failed: %s", candidate.path)
                video = _upsert_video(db, lesson, candidate)
                video.scrape_status = "failed"
                video.scrape_error = str(exc)
                job.failed_videos += 1
                add_event(
                    db,
                    job,
                    f"Failed {candidate.title}: {exc}",
                    "error",
                    {"path": candidate.path},
                )

            attempted = job.processed_videos + job.failed_videos
            job.progress_percent = (
                round(attempted * 100 / job.total_videos) if job.total_videos else 100
            )
            job.current_step = f"Processed {attempted} of {job.total_videos} videos"
            db.commit()
            if settings.request_delay_seconds and index < len(candidates):
                time.sleep(settings.request_delay_seconds)

        job = db.get(ScrapeJob, job_id)
        job.progress_percent = 100
        job.finished_at = now()
        job.status = (
            JobStatus.completed_with_errors
            if job.failed_videos
            else JobStatus.completed
        )
        job.current_step = "Finished"
        add_event(db, job, f"Job finished with status {job.status.value}")
        db.commit()
    except Exception as exc:
        logger.exception("Job failed: %s", job_id)
        db.rollback()
        job = db.get(ScrapeJob, job_id)
        if job is not None:
            job.status = JobStatus.failed
            job.current_step = "Failed"
            job.error_message = str(exc)
            job.finished_at = now()
            add_event(db, job, f"Job failed: {exc}", "error")
            db.commit()
    finally:
        db.close()

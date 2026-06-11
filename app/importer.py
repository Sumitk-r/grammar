from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Course,
    JobEvent,
    JobStatus,
    Lesson,
    ScrapeJob,
    Transcript,
    Unit,
    Video,
)


def text_or_none(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(float(value)) if value else None


def import_transcript_csv(
    db: Session,
    csv_path: str | Path,
    course_title: str = "Grammar",
    course_url: str = "https://www.khanacademy.org/humanities/grammar",
) -> tuple[Course, int]:
    csv_path = Path(csv_path)
    relative_url = urlparse(course_url).path.rstrip("/")
    course = db.scalar(select(Course).where(Course.relative_url == relative_url))
    if course is None:
        course = Course(
            title=course_title,
            slug=relative_url.split("/")[-1],
            relative_url=relative_url,
            source_url=course_url,
            description="Khan Academy Grammar video transcripts.",
        )
        db.add(course)
        db.flush()

    units: dict[int, Unit] = {}
    lessons: dict[tuple[int, int], Lesson] = {}
    imported = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            unit_index = int(row["unit_index"])
            lesson_index = int(row["lesson_index"])
            video_index = int(row["video_index"])

            unit = units.get(unit_index)
            if unit is None:
                unit = db.scalar(
                    select(Unit).where(
                        Unit.course_id == course.id,
                        Unit.unit_index == unit_index,
                    )
                )
                if unit is None:
                    unit = Unit(
                        course=course,
                        unit_index=unit_index,
                        title=row["unit_title"].strip(),
                    )
                    db.add(unit)
                    db.flush()
                else:
                    unit.title = row["unit_title"].strip()
                units[unit_index] = unit

            lesson_key = (unit_index, lesson_index)
            lesson = lessons.get(lesson_key)
            if lesson is None:
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
                        title=row["lesson_title"].strip(),
                    )
                    db.add(lesson)
                    db.flush()
                else:
                    lesson.title = row["lesson_title"].strip()
                lessons[lesson_key] = lesson

            video_url = row["video_url"].strip()
            video_path = urlparse(video_url).path.rstrip("/")
            video = db.scalar(select(Video).where(Video.relative_url == video_path))
            if video is None:
                video = Video(
                    lesson=lesson,
                    video_index=video_index,
                    title=row["video_title"].strip(),
                    relative_url=video_path,
                    full_url=video_url,
                    content_kind=row.get("content_kind", "Video").strip() or "Video",
                )
                db.add(video)
            video.lesson = lesson
            video.video_index = video_index
            video.title = row["video_title"].strip()
            video.full_url = video_url
            video.youtube_id = text_or_none(row.get("youtube_id"))
            video.duration_seconds = int_or_none(row.get("duration_seconds"))
            video.scrape_status = "completed" if row.get("transcript", "").strip() else "no_transcript"
            video.scrape_error = None
            db.flush()

            transcript_text = row.get("transcript", "").strip()
            if transcript_text:
                if video.transcript is None:
                    db.add(
                        Transcript(
                            video=video,
                            plain_text=transcript_text,
                            source="csv_import",
                        )
                    )
                else:
                    video.transcript.plain_text = transcript_text
                    video.transcript.source = "csv_import"
                imported += 1

    job = db.scalar(
        select(ScrapeJob)
        .where(
            ScrapeJob.normalized_path == relative_url,
            ScrapeJob.status == JobStatus.completed,
        )
        .order_by(ScrapeJob.created_at.desc())
    )
    if job is None:
        finished_at = datetime.now(timezone.utc)
        job = ScrapeJob(
            submitted_url=course_url,
            normalized_path=relative_url,
            status=JobStatus.completed,
            current_step="Imported from CSV",
            progress_percent=100,
            total_videos=imported,
            processed_videos=imported,
            failed_videos=0,
            course=course,
            started_at=finished_at,
            finished_at=finished_at,
        )
        db.add(job)
        db.flush()
        db.add(
            JobEvent(
                job=job,
                message=f"Imported {imported} transcripts from {csv_path.name}",
            )
        )

    db.commit()
    db.refresh(course)
    return course, imported


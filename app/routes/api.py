from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    Course,
    JobStatus,
    Lesson,
    ScrapeJob,
    Transcript,
    Unit,
    Video,
)
from app.schemas import JobCreate, JobCreated, TranscriptRead
from app.services.urls import InvalidCourseUrl, validate_course_url

router = APIRouter(prefix="/api")


def job_dict(job: ScrapeJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "submitted_url": job.submitted_url,
        "normalized_path": job.normalized_path,
        "status": job.status.value,
        "current_step": job.current_step,
        "progress_percent": job.progress_percent,
        "total_videos": job.total_videos,
        "processed_videos": job.processed_videos,
        "failed_videos": job.failed_videos,
        "error_message": job.error_message,
        "course_id": job.course_id,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def get_or_404(db: Session, model: type, item_id: str):
    item = db.get(model, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return item


@router.post("/jobs", response_model=JobCreated, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: Session = Depends(get_db)) -> JobCreated:
    try:
        course_url = validate_course_url(payload.url)
    except InvalidCourseUrl as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    active = db.scalar(
        select(ScrapeJob)
        .where(
            ScrapeJob.normalized_path == course_url.normalized_path,
            ScrapeJob.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .order_by(ScrapeJob.created_at.desc())
    )
    if active:
        return JobCreated(job_id=active.id, status=active.status, reused=True)

    job = ScrapeJob(
        submitted_url=course_url.submitted_url,
        normalized_path=course_url.normalized_path,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobCreated(job_id=job.id, status=job.status)


@router.get("/jobs")
def list_jobs(
    status_filter: JobStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = []
    if status_filter:
        conditions.append(ScrapeJob.status == status_filter)
    query = select(ScrapeJob).where(*conditions)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    jobs = db.scalars(
        query.order_by(ScrapeJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [job_dict(job) for job in jobs],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return job_dict(get_or_404(db, ScrapeJob, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = get_or_404(db, ScrapeJob, job_id)
    if job.status not in {JobStatus.queued, JobStatus.running}:
        raise HTTPException(status_code=409, detail="This job can no longer be cancelled.")
    job.status = JobStatus.cancelled
    job.current_step = "Cancellation requested"
    db.commit()
    return job_dict(job)


@router.get("/jobs/{job_id}/course")
def get_job_course(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = get_or_404(db, ScrapeJob, job_id)
    if not job.course_id:
        raise HTTPException(status_code=404, detail="This job does not have a course result yet.")
    course = get_or_404(db, Course, job.course_id)
    return {
        "job_id": job.id,
        "course_id": course.id,
        "title": course.title,
        "source_url": course.source_url,
    }


@router.get("/courses")
def list_courses(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    courses = db.scalars(select(Course).order_by(Course.updated_at.desc())).all()
    return [
        {
            "id": course.id,
            "title": course.title,
            "slug": course.slug,
            "source_url": course.source_url,
        }
        for course in courses
    ]


@router.get("/courses/{course_id}")
def get_course(course_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    course = get_or_404(db, Course, course_id)
    return {
        "id": course.id,
        "title": course.title,
        "slug": course.slug,
        "description": course.description,
        "relative_url": course.relative_url,
        "source_url": course.source_url,
    }


@router.get("/courses/{course_id}/units")
def get_course_units(course_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    get_or_404(db, Course, course_id)
    units = db.scalars(
        select(Unit).where(Unit.course_id == course_id).order_by(Unit.unit_index)
    ).all()
    return [
        {"id": unit.id, "unit_index": unit.unit_index, "title": unit.title}
        for unit in units
    ]


@router.get("/units/{unit_id}/lessons")
def get_unit_lessons(unit_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    get_or_404(db, Unit, unit_id)
    lessons = db.scalars(
        select(Lesson).where(Lesson.unit_id == unit_id).order_by(Lesson.lesson_index)
    ).all()
    return [
        {"id": lesson.id, "lesson_index": lesson.lesson_index, "title": lesson.title}
        for lesson in lessons
    ]


@router.get("/lessons/{lesson_id}/videos")
def get_lesson_videos(lesson_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    get_or_404(db, Lesson, lesson_id)
    videos = db.scalars(
        select(Video).where(Video.lesson_id == lesson_id).order_by(Video.video_index)
    ).all()
    return [
        {
            "id": video.id,
            "video_index": video.video_index,
            "title": video.title,
            "full_url": video.full_url,
            "youtube_id": video.youtube_id,
            "duration_seconds": video.duration_seconds,
            "scrape_status": video.scrape_status,
        }
        for video in videos
    ]


@router.get("/videos/{video_id}")
def get_video(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = get_or_404(db, Video, video_id)
    return {
        "id": video.id,
        "lesson_id": video.lesson_id,
        "title": video.title,
        "relative_url": video.relative_url,
        "full_url": video.full_url,
        "youtube_id": video.youtube_id,
        "duration_seconds": video.duration_seconds,
        "content_kind": video.content_kind,
        "scrape_status": video.scrape_status,
        "scrape_error": video.scrape_error,
    }


@router.get("/videos/{video_id}/transcript", response_model=TranscriptRead)
def get_transcript(video_id: str, db: Session = Depends(get_db)) -> TranscriptRead:
    video = db.scalar(
        select(Video)
        .where(Video.id == video_id)
        .options(selectinload(Video.transcript).selectinload(Transcript.segments))
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return TranscriptRead(
        video_id=video.id,
        title=video.title,
        video_url=video.full_url,
        plain_text=video.transcript.plain_text,
        segments=[
            {
                "start_time_seconds": segment.start_time_seconds,
                "end_time_seconds": segment.end_time_seconds,
                "text": segment.text,
            }
            for segment in video.transcript.segments
        ],
    )


@router.get("/search")
def search_transcripts(
    course_id: str,
    q: str = Query("", max_length=200),
    unit_id: str | None = None,
    lesson_id: str | None = None,
    failed_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    get_or_404(db, Course, course_id)
    query = (
        select(Video, Lesson, Unit, Transcript)
        .join(Lesson, Video.lesson_id == Lesson.id)
        .join(Unit, Lesson.unit_id == Unit.id)
        .outerjoin(Transcript, Transcript.video_id == Video.id)
        .where(Unit.course_id == course_id)
    )
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Video.title.ilike(pattern),
                Lesson.title.ilike(pattern),
                Unit.title.ilike(pattern),
                Transcript.plain_text.ilike(pattern),
            )
        )
    if unit_id:
        query = query.where(Unit.id == unit_id)
    if lesson_id:
        query = query.where(Lesson.id == lesson_id)
    if failed_only:
        query = query.where(Video.scrape_status == "failed")

    rows = db.execute(
        query.order_by(Unit.unit_index, Lesson.lesson_index, Video.video_index).limit(limit)
    ).all()
    return [
        {
            "video_id": video.id,
            "video_title": video.title,
            "unit_id": unit.id,
            "unit_title": unit.title,
            "lesson_id": lesson.id,
            "lesson_title": lesson.title,
            "scrape_status": video.scrape_status,
            "excerpt": transcript.plain_text[:320] if transcript else "",
        }
        for video, lesson, unit, transcript in rows
    ]


def export_rows(db: Session, course_id: str):
    return db.execute(
        select(Video, Lesson, Unit, Transcript)
        .join(Lesson, Video.lesson_id == Lesson.id)
        .join(Unit, Lesson.unit_id == Unit.id)
        .outerjoin(Transcript, Transcript.video_id == Video.id)
        .where(Unit.course_id == course_id)
        .order_by(Unit.unit_index, Lesson.lesson_index, Video.video_index)
    ).all()


@router.get("/courses/{course_id}/export.csv")
def export_course_csv(course_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    course = get_or_404(db, Course, course_id)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "unit_index",
            "unit_title",
            "lesson_index",
            "lesson_title",
            "video_index",
            "video_title",
            "video_url",
            "content_kind",
            "youtube_id",
            "duration_seconds",
            "scrape_status",
            "transcript",
        ],
    )
    writer.writeheader()
    for video, lesson, unit, transcript in export_rows(db, course_id):
        writer.writerow(
            {
                "unit_index": unit.unit_index,
                "unit_title": unit.title,
                "lesson_index": lesson.lesson_index,
                "lesson_title": lesson.title,
                "video_index": video.video_index,
                "video_title": video.title,
                "video_url": video.full_url,
                "content_kind": video.content_kind,
                "youtube_id": video.youtube_id or "",
                "duration_seconds": video.duration_seconds or "",
                "scrape_status": video.scrape_status,
                "transcript": transcript.plain_text if transcript else "",
            }
        )
    content = output.getvalue().encode("utf-8")
    filename = f"{course.slug or 'course'}-transcripts.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/courses/{course_id}/export.json")
def export_course_json(course_id: str, db: Session = Depends(get_db)) -> Response:
    course = get_or_404(db, Course, course_id)
    records = []
    for video, lesson, unit, transcript in export_rows(db, course_id):
        records.append(
            {
                "unit_index": unit.unit_index,
                "unit_title": unit.title,
                "lesson_index": lesson.lesson_index,
                "lesson_title": lesson.title,
                "video_index": video.video_index,
                "video_title": video.title,
                "video_url": video.full_url,
                "youtube_id": video.youtube_id,
                "duration_seconds": video.duration_seconds,
                "scrape_status": video.scrape_status,
                "transcript": transcript.plain_text if transcript else "",
            }
        )
    return Response(
        json.dumps(
            {"course": {"id": course.id, "title": course.title}, "videos": records},
            ensure_ascii=False,
            indent=2,
        ),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{course.slug or "course"}-transcripts.json"'
        },
    )


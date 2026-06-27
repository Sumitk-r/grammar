from __future__ import annotations

import csv
import hmac
import io
import json
import re
import subprocess
import sys
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    Category,
    Course,
    JobEvent,
    JobStatus,
    Lesson,
    ScrapeJob,
    Transcript,
    TranscriptEmbedding,
    TranscriptSegment,
    Unit,
    Video,
)
from app.schemas import CategoryCreate, CategoryRead, JobCreate, JobCreated, TranscriptRead
from app.services.embeddings import EMBEDDING_MODEL
from app.services.job_processor import audio_transcript_path
from app.services.pgvector_search import hybrid_search_transcripts, pgvector_available
from app.services.urls import InvalidCourseUrl, validate_course_url
from app.services.youtube_backfill import backfill_missing_youtube_captions

router = APIRouter(prefix="/api")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "category"


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
        "category_id": job.category_id,
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


def category_dict(category: Category) -> dict[str, Any]:
    return {
        "id": category.id,
        "name": category.name,
        "slug": category.slug,
        "description": category.description,
    }


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_key):
        raise HTTPException(status_code=401, detail="Admin key is required.")


def queue_audio_transcript_job(db: Session, video: Video) -> tuple[ScrapeJob, bool]:
    normalized_path = audio_transcript_path(video.id)
    active = db.scalar(
        select(ScrapeJob)
        .where(
            ScrapeJob.normalized_path == normalized_path,
            ScrapeJob.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .order_by(ScrapeJob.created_at.desc())
    )
    if active:
        return active, True

    job = ScrapeJob(
        submitted_url=video.full_url,
        normalized_path=normalized_path,
        course_id=video.lesson.unit.course_id,
        total_videos=1,
        current_step="Waiting for worker",
    )
    video.scrape_status = "queued_transcription"
    video.scrape_error = None
    db.add(job)
    return job, False


def ytdlp_version() -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    return [category_dict(category) for category in categories]


@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    name = payload.name.strip()
    slug = slugify(name)
    existing = db.scalar(
        select(Category).where(
            or_(Category.name == name, Category.slug == slug),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Category already exists.")

    category = Category(
        name=name,
        slug=slug,
        description=payload.description.strip() if payload.description else None,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category_dict(category)


@router.post("/jobs", response_model=JobCreated, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: Session = Depends(get_db)) -> JobCreated:
    category = None
    if payload.category_id:
        category = db.get(Category, payload.category_id)
        if category is None:
            raise HTTPException(status_code=404, detail="Category not found.")

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
        category=category,
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


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = get_or_404(db, ScrapeJob, job_id)
    if job.status in {JobStatus.queued, JobStatus.running}:
        raise HTTPException(status_code=409, detail="This job is already active.")
    job.status = JobStatus.queued
    job.current_step = "Waiting for worker"
    job.progress_percent = 0
    job.processed_videos = 0
    job.failed_videos = 0
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    add_event = JobEvent(job=job, message="Job manually queued for retry")
    db.add(add_event)
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
            "category": category_dict(course.category) if course.category else None,
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
        "category": category_dict(course.category) if course.category else None,
    }


@router.delete("/courses/{course_id}")
def delete_course(
    course_id: str,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    course = get_or_404(db, Course, course_id)
    active_job = db.scalar(
        select(ScrapeJob.id).where(
            ScrapeJob.course_id == course_id,
            ScrapeJob.status.in_([JobStatus.queued, JobStatus.running]),
        )
    )
    if active_job:
        raise HTTPException(
            status_code=409,
            detail="This course has an active job. Cancel or wait for it before deleting.",
        )

    unit_ids = db.scalars(select(Unit.id).where(Unit.course_id == course_id)).all()
    lesson_ids = (
        db.scalars(select(Lesson.id).where(Lesson.unit_id.in_(unit_ids))).all()
        if unit_ids
        else []
    )
    video_ids = (
        db.scalars(select(Video.id).where(Video.lesson_id.in_(lesson_ids))).all()
        if lesson_ids
        else []
    )
    transcript_ids = (
        db.scalars(select(Transcript.id).where(Transcript.video_id.in_(video_ids))).all()
        if video_ids
        else []
    )
    job_ids = db.scalars(select(ScrapeJob.id).where(ScrapeJob.course_id == course_id)).all()

    if transcript_ids:
        db.query(TranscriptEmbedding).filter(
            TranscriptEmbedding.transcript_id.in_(transcript_ids)
        ).delete(synchronize_session=False)
        db.query(TranscriptSegment).filter(
            TranscriptSegment.transcript_id.in_(transcript_ids)
        ).delete(synchronize_session=False)
        db.query(Transcript).filter(Transcript.id.in_(transcript_ids)).delete(
            synchronize_session=False
        )
    if video_ids:
        db.query(Video).filter(Video.id.in_(video_ids)).delete(synchronize_session=False)
    if lesson_ids:
        db.query(Lesson).filter(Lesson.id.in_(lesson_ids)).delete(
            synchronize_session=False
        )
    if unit_ids:
        db.query(Unit).filter(Unit.id.in_(unit_ids)).delete(synchronize_session=False)
    if job_ids:
        db.query(JobEvent).filter(JobEvent.job_id.in_(job_ids)).delete(
            synchronize_session=False
        )
        db.query(ScrapeJob).filter(ScrapeJob.id.in_(job_ids)).delete(
            synchronize_session=False
        )

    db.delete(course)
    db.commit()
    return {
        "status": "deleted",
        "course_id": course_id,
        "deleted": {
            "units": len(unit_ids),
            "lessons": len(lesson_ids),
            "videos": len(video_ids),
            "transcripts": len(transcript_ids),
            "jobs": len(job_ids),
        },
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


@router.post("/courses/{course_id}/youtube-backfill", status_code=202)
def start_youtube_backfill(
    course_id: str,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    get_or_404(db, Course, course_id)
    background_tasks.add_task(backfill_missing_youtube_captions, course_id)
    return {
        "status": "started",
        "message": "Fetching missing YouTube transcripts in the background.",
    }


@router.post("/courses/{course_id}/generate-missing-transcripts", status_code=202)
def generate_missing_transcripts(
    course_id: str,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    get_or_404(db, Course, course_id)
    videos = db.scalars(
        select(Video)
        .join(Lesson)
        .join(Unit)
        .where(
            Unit.course_id == course_id,
            Video.youtube_id.is_not(None),
            Video.transcript == None,  # noqa: E711
        )
        .options(selectinload(Video.lesson).selectinload(Lesson.unit))
        .order_by(Unit.unit_index, Lesson.lesson_index, Video.video_index)
    ).all()
    queued = 0
    reused = 0
    for video in videos:
        _, was_reused = queue_audio_transcript_job(db, video)
        if was_reused:
            reused += 1
        else:
            queued += 1
    db.commit()
    return {
        "status": "queued",
        "queued": queued,
        "reused": reused,
        "eligible_videos": len(videos),
    }


@router.post("/videos/{video_id}/generate-transcript", status_code=status.HTTP_202_ACCEPTED)
def generate_video_transcript(
    video_id: str,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not settings.audio_transcription_enabled:
        raise HTTPException(status_code=503, detail="Audio transcription is disabled.")

    video = db.scalar(
        select(Video)
        .where(Video.id == video_id)
        .options(
            selectinload(Video.transcript),
            selectinload(Video.lesson).selectinload(Lesson.unit),
        )
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.transcript is not None:
        raise HTTPException(status_code=409, detail="This video already has a transcript.")
    if not video.youtube_id:
        raise HTTPException(
            status_code=400,
            detail="Only YouTube-backed videos can be transcribed from audio.",
        )

    job, reused = queue_audio_transcript_job(db, video)
    db.commit()
    db.refresh(job)
    return {
        "status": job.status.value,
        "job_id": job.id,
        "reused": reused,
        "message": (
            "Transcript generation is already queued or running."
            if reused
            else "Transcript generation queued."
        ),
    }


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
        language_code=video.transcript.language_code,
        source=video.transcript.source,
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
    course_id: str | None = None,
    category_id: str | None = None,
    q: str = Query("", max_length=200),
    unit_id: str | None = None,
    lesson_id: str | None = None,
    transcript_source: str | None = None,
    scrape_status: str | None = None,
    failed_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if course_id:
        get_or_404(db, Course, course_id)
    if category_id:
        get_or_404(db, Category, category_id)
    if q.strip() and not failed_only:
        hybrid_results = hybrid_search_transcripts(
            db,
            course_id=course_id,
            category_id=category_id,
            query=q.strip(),
            unit_id=unit_id,
            lesson_id=lesson_id,
            transcript_source=transcript_source,
            limit=limit,
        )
        if hybrid_results is not None and not scrape_status:
            return hybrid_results

    query = (
        select(Video, Lesson, Unit, Course, Category, Transcript)
        .join(Lesson, Video.lesson_id == Lesson.id)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Course, Unit.course_id == Course.id)
        .outerjoin(Category, Course.category_id == Category.id)
        .outerjoin(Transcript, Transcript.video_id == Video.id)
    )
    if course_id:
        query = query.where(Unit.course_id == course_id)
    if category_id:
        query = query.where(Course.category_id == category_id)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Course.title.ilike(pattern),
                Category.name.ilike(pattern),
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
    if transcript_source:
        query = query.where(Transcript.source == transcript_source)
    if scrape_status:
        query = query.where(Video.scrape_status == scrape_status)
    if failed_only:
        query = query.where(Video.scrape_status == "failed")

    rows = db.execute(
        query.order_by(
            Course.updated_at.desc(),
            Unit.unit_index,
            Lesson.lesson_index,
            Video.video_index,
        ).limit(limit)
    ).all()
    return [
        {
            "video_id": video.id,
            "video_title": video.title,
            "course_id": course.id,
            "course_title": course.title,
            "category_id": category.id if category else None,
            "category_name": category.name if category else None,
            "unit_id": unit.id,
            "unit_title": unit.title,
            "lesson_id": lesson.id,
            "lesson_title": lesson.title,
            "scrape_status": video.scrape_status,
            "transcript_source": transcript.source if transcript else None,
            "excerpt": transcript.plain_text[:320] if transcript else "",
            "highlighted_snippet": transcript.plain_text[:320] if transcript else "",
            "start_time_seconds": None,
            "end_time_seconds": None,
            "source_url": video.full_url,
            "search_mode": "text",
            "lexical_score": 0,
            "semantic_score": 0,
            "relevance_score": 0,
        }
        for video, lesson, unit, course, category, transcript in rows
    ]


@router.get("/system/search-status")
def search_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    embedding_count = db.scalar(select(func.count(TranscriptEmbedding.id))) or 0
    timestamp_count = (
        db.scalar(
            select(func.count(TranscriptEmbedding.id)).where(
                TranscriptEmbedding.start_time_seconds.is_not(None)
            )
        )
        or 0
    )
    return {
        "database": db.get_bind().dialect.name,
        "pgvector_enabled": pgvector_available(db),
        "embedding_provider": "local_hash",
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": settings.embedding_dimensions,
        "stored_chunks": embedding_count,
        "timestamped_chunks": timestamp_count,
        "yt_dlp_version": ytdlp_version(),
        "audio_transcription_enabled": settings.audio_transcription_enabled,
    }


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

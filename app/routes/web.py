from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings
from app.database import get_db
from app.models import Category, Course, JobStatus, Lesson, ScrapeJob, Transcript, Unit, Video
from app.services.pgvector_search import pgvector_available

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _display_zone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.display_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def local_datetime(value, fmt: str = "%b %d, %Y %H:%M") -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local_value = value.astimezone(_display_zone())
    zone_label = local_value.tzname() or settings.display_timezone
    return f"{local_value.strftime(fmt)} {zone_label}"


templates.env.filters["local_datetime"] = local_datetime


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(ScrapeJob).order_by(ScrapeJob.created_at.desc()).limit(10)
    ).all()
    categories = db.scalars(
        select(Category)
        .options(selectinload(Category.courses))
        .order_by(Category.name)
    ).all()
    category_groups = [
        {
            "category": category,
            "courses": sorted(
                category.courses,
                key=lambda course: course.updated_at,
                reverse=True,
            ),
        }
        for category in categories
        if category.courses
    ]
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "jobs": jobs,
            "categories": categories,
            "category_groups": category_groups,
            "search_status": {
                "pgvector_enabled": pgvector_available(db),
                "embedding_provider": "local_hash",
            },
        },
    )


@router.get("/admin/jobs", response_class=HTMLResponse)
def admin_jobs_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(ScrapeJob).order_by(ScrapeJob.created_at.desc()).limit(100)
    ).all()
    counts = dict(
        db.execute(select(ScrapeJob.status, func.count(ScrapeJob.id)).group_by(ScrapeJob.status)).all()
    )
    return templates.TemplateResponse(
        request,
        "admin_jobs.html",
        {
            "jobs": jobs,
            "counts": counts,
            "statuses": list(JobStatus),
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = db.scalar(
        select(ScrapeJob)
        .where(ScrapeJob.id == job_id)
        .options(selectinload(ScrapeJob.events))
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(request, "job.html", {"job": job})


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_page(course_id: str, request: Request, db: Session = Depends(get_db)):
    course = db.scalar(
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.category),
            selectinload(Course.units)
            .selectinload(Unit.lessons)
            .selectinload(Lesson.videos)
            .selectinload(Video.transcript)
        )
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    video_count = db.scalar(
        select(func.count(Video.id))
        .join(Lesson)
        .join(Unit)
        .where(Unit.course_id == course_id)
    )
    transcript_count = db.scalar(
        select(func.count(Transcript.id))
        .join(Video)
        .join(Lesson)
        .join(Unit)
        .where(Unit.course_id == course_id)
    )
    source_counts = dict(
        db.execute(
            select(Transcript.source, func.count(Transcript.id))
            .join(Video)
            .join(Lesson)
            .join(Unit)
            .where(Unit.course_id == course_id)
            .group_by(Transcript.source)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "course.html",
        {
            "course": course,
            "video_count": video_count,
            "transcript_count": transcript_count,
            "source_counts": source_counts,
        },
    )


@router.get("/videos/{video_id}", response_class=HTMLResponse)
def video_page(video_id: str, request: Request, db: Session = Depends(get_db)):
    video = db.scalar(
        select(Video)
        .where(Video.id == video_id)
        .options(
            selectinload(Video.transcript).selectinload(Transcript.segments),
            selectinload(Video.lesson).selectinload(Lesson.unit),
        )
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return templates.TemplateResponse(request, "video.html", {"video": video})

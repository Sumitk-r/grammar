from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Course, Lesson, ScrapeJob, Transcript, Unit, Video

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(ScrapeJob).order_by(ScrapeJob.created_at.desc()).limit(10)
    ).all()
    courses = db.scalars(
        select(Course).order_by(Course.updated_at.desc()).limit(6)
    ).all()
    return templates.TemplateResponse(
        request,
        "home.html",
        {"jobs": jobs, "courses": courses},
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
    return templates.TemplateResponse(
        request,
        "course.html",
        {
            "course": course,
            "video_count": video_count,
            "transcript_count": transcript_count,
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


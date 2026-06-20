from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_string() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    completed_with_errors = "completed_with_errors"


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    courses: Mapped[list[Course]] = relationship(back_populates="category")
    jobs: Mapped[list[ScrapeJob]] = relationship(back_populates="category")


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    submitted_url: Mapped[str] = mapped_column(Text)
    normalized_path: Mapped[str] = mapped_column(Text, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.queued, index=True
    )
    current_step: Mapped[str] = mapped_column(String(255), default="Waiting for worker")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    total_videos: Mapped[int] = mapped_column(Integer, default=0)
    processed_videos: Mapped[int] = mapped_column(Integer, default=0)
    failed_videos: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"), index=True)
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    category: Mapped[Category | None] = relationship(back_populates="jobs")
    course: Mapped[Course | None] = relationship(back_populates="jobs")
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.created_at"
    )


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"), index=True)
    khan_course_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    slug: Mapped[str] = mapped_column(String(255))
    relative_url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    source_url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    category: Mapped[Category | None] = relationship(back_populates="courses")
    units: Mapped[list[Unit]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="Unit.unit_index"
    )
    jobs: Mapped[list[ScrapeJob]] = relationship(back_populates="course")


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("course_id", "unit_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    khan_unit_id: Mapped[str | None] = mapped_column(String(255))
    unit_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    slug: Mapped[str | None] = mapped_column(String(255))
    relative_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    course: Mapped[Course] = relationship(back_populates="units")
    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="unit", cascade="all, delete-orphan", order_by="Lesson.lesson_index"
    )


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("unit_id", "lesson_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    khan_lesson_id: Mapped[str | None] = mapped_column(String(255))
    lesson_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    slug: Mapped[str | None] = mapped_column(String(255))
    relative_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    unit: Mapped[Unit] = relationship(back_populates="lessons")
    videos: Mapped[list[Video]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan", order_by="Video.video_index"
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    video_index: Mapped[int] = mapped_column(Integer)
    khan_video_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    readable_id: Mapped[str | None] = mapped_column(String(255))
    relative_url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    full_url: Mapped[str] = mapped_column(Text)
    youtube_id: Mapped[str | None] = mapped_column(String(64))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    content_kind: Mapped[str] = mapped_column(String(64), default="Video")
    scrape_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    scrape_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    lesson: Mapped[Lesson] = relationship(back_populates="videos")
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="video", cascade="all, delete-orphan", uselist=False
    )


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), unique=True, index=True)
    plain_text: Mapped[str] = mapped_column(Text)
    language_code: Mapped[str] = mapped_column(String(16), default="en")
    source: Mapped[str] = mapped_column(String(64), default="khan_subtitles")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    video: Mapped[Video] = relationship(back_populates="transcript")
    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="TranscriptSegment.segment_index",
    )
    embeddings: Mapped[list[TranscriptEmbedding]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="TranscriptEmbedding.chunk_index",
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("transcript_id", "segment_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id"), index=True)
    segment_index: Mapped[int] = mapped_column(Integer)
    start_time_seconds: Mapped[float | None] = mapped_column(Float)
    end_time_seconds: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")


class TranscriptEmbedding(Base):
    __tablename__ = "transcript_embeddings"
    __table_args__ = (UniqueConstraint("transcript_id", "chunk_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transcript: Mapped[Transcript] = relationship(back_populates="embeddings")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    job_id: Mapped[str] = mapped_column(ForeignKey("scrape_jobs.id"), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[ScrapeJob] = relationship(back_populates="events")

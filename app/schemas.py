from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import JobStatus


class JobCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    category_id: str | None = Field(default=None, min_length=1, max_length=36)


class JobCreated(BaseModel):
    job_id: str
    status: JobStatus
    reused: bool = False


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class CategoryRead(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    submitted_url: str
    normalized_path: str
    status: JobStatus
    current_step: str
    progress_percent: int
    total_videos: int
    processed_videos: int
    failed_videos: int
    error_message: str | None
    course_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SegmentRead(BaseModel):
    start_time_seconds: float | None
    end_time_seconds: float | None
    text: str


class TranscriptRead(BaseModel):
    video_id: str
    title: str
    video_url: str
    language_code: str
    source: str
    plain_text: str
    segments: list[SegmentRead]

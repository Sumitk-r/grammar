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
from app.services.audio_transcription import (
    AudioTranscriptionUnavailable,
    FasterWhisperTranscriber,
)
from app.services.khan_client import KhanClient, VideoCandidate, full_url
from app.services.pgvector_search import sync_pgvector_embeddings
from app.services.transcript_chunks import transcript_chunk_rows
from app.services.urls import is_youtube_playlist_path, youtube_playlist_id_from_path
from app.services.youtube_client import (
    YouTubeCaptionClient,
    YouTubeCaptionUnavailable,
)
from app.services.youtube_playlist_client import (
    YouTubePlaylistClient,
    YouTubePlaylistData,
    YouTubePlaylistVideo,
)

logger = logging.getLogger(__name__)
AUDIO_TRANSCRIPT_PREFIX = "audio_transcript:"


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


def _upsert_course(
    db: Session,
    payload: dict[str, Any],
    path: str,
    category_id: str | None,
) -> Course:
    relative_url = payload.get("relativeUrl") or path
    course = db.scalar(select(Course).where(Course.relative_url == relative_url))
    if course is None:
        course = Course(relative_url=relative_url, source_url=full_url(relative_url))
        db.add(course)
    course.khan_course_id = payload.get("id")
    course.category_id = category_id
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
        transcript.embeddings.clear()
    transcript.source = source
    transcript.language_code = language_code
    db.flush()
    for segment in segments:
        transcript.segments.append(TranscriptSegment(**segment))
    for chunk in transcript_chunk_rows(transcript, video, plain_text, segments):
        transcript.embeddings.append(chunk)
    db.flush()
    sync_pgvector_embeddings(db, transcript.embeddings)


def _youtube_languages() -> list[str]:
    return [
        language.strip()
        for language in settings.youtube_languages.split(",")
        if language.strip()
    ]


def audio_transcript_path(video_id: str) -> str:
    return f"{AUDIO_TRANSCRIPT_PREFIX}{video_id}"


def is_audio_transcript_path(value: str) -> bool:
    return value.startswith(AUDIO_TRANSCRIPT_PREFIX)


def video_id_from_audio_transcript_path(value: str) -> str:
    return value.removeprefix(AUDIO_TRANSCRIPT_PREFIX)


def _upsert_youtube_playlist_course(
    db: Session,
    playlist: YouTubePlaylistData,
    category_id: str | None,
) -> tuple[Course, Lesson]:
    normalized_path = f"youtube_playlist:{playlist.playlist_id}"
    course = db.scalar(select(Course).where(Course.relative_url == normalized_path))
    if course is None:
        course = Course(
            relative_url=normalized_path,
            source_url=playlist.source_url,
            title=playlist.title,
            slug=playlist.playlist_id,
        )
        db.add(course)
    course.khan_course_id = None
    course.category_id = category_id
    course.title = playlist.title
    course.slug = playlist.playlist_id
    course.source_url = playlist.source_url
    course.description = playlist.description or "YouTube playlist transcript collection."
    db.flush()

    unit = db.scalar(
        select(Unit).where(
            Unit.course_id == course.id,
            Unit.unit_index == 1,
        )
    )
    if unit is None:
        unit = Unit(course=course, unit_index=1, title="YouTube playlist")
        db.add(unit)
    unit.khan_unit_id = None
    unit.title = "YouTube playlist"
    unit.slug = "youtube-playlist"
    unit.relative_url = normalized_path
    db.flush()

    lesson = db.scalar(
        select(Lesson).where(
            Lesson.unit_id == unit.id,
            Lesson.lesson_index == 1,
        )
    )
    if lesson is None:
        lesson = Lesson(unit=unit, lesson_index=1, title="Videos")
        db.add(lesson)
    lesson.khan_lesson_id = None
    lesson.title = "Videos"
    lesson.slug = "videos"
    lesson.relative_url = normalized_path
    db.flush()
    return course, lesson


def _upsert_youtube_playlist_video(
    db: Session,
    lesson: Lesson,
    playlist_id: str,
    playlist_video: YouTubePlaylistVideo,
) -> Video:
    relative_url = f"youtube:playlist:{playlist_id}:video:{playlist_video.video_id}"
    video = db.scalar(select(Video).where(Video.relative_url == relative_url))
    if video is None:
        video = Video(
            lesson=lesson,
            video_index=playlist_video.video_index,
            title=playlist_video.title,
            relative_url=relative_url,
            full_url=playlist_video.full_url,
            youtube_id=playlist_video.video_id,
            duration_seconds=playlist_video.duration_seconds,
            content_kind="YouTubeVideo",
        )
        db.add(video)
    else:
        video.lesson = lesson
        video.video_index = playlist_video.video_index
    video.khan_video_id = None
    video.title = playlist_video.title
    video.full_url = playlist_video.full_url
    video.youtube_id = playlist_video.video_id
    video.duration_seconds = playlist_video.duration_seconds
    video.content_kind = "YouTubeVideo"
    return video


def _process_youtube_playlist_job(
    db: Session,
    job_id: str,
    youtube_client: YouTubeCaptionClient | None,
    youtube_playlist_client: YouTubePlaylistClient | None,
) -> None:
    if youtube_client is None:
        youtube_client = YouTubeCaptionClient(_youtube_languages())
    youtube_playlist_client = youtube_playlist_client or YouTubePlaylistClient()

    job = db.get(ScrapeJob, job_id)
    playlist_id = youtube_playlist_id_from_path(job.normalized_path)
    job.current_step = "Fetching YouTube playlist"
    db.commit()

    playlist = youtube_playlist_client.fetch_playlist(playlist_id)
    if settings.max_videos_per_job is not None:
        playlist = YouTubePlaylistData(
            playlist_id=playlist.playlist_id,
            title=playlist.title,
            source_url=playlist.source_url,
            description=playlist.description,
            videos=playlist.videos[: settings.max_videos_per_job],
        )

    job = db.get(ScrapeJob, job_id)
    course, lesson = _upsert_youtube_playlist_course(db, playlist, job.category_id)
    job.course = course
    job.total_videos = len(playlist.videos)
    job.current_step = "Processing YouTube captions"
    add_event(db, job, f"Found {len(playlist.videos)} YouTube playlist videos")
    db.commit()

    for index, playlist_video in enumerate(playlist.videos, 1):
        job = db.get(ScrapeJob, job_id)
        if job.status == JobStatus.cancelled:
            job.current_step = "Cancelled"
            job.finished_at = now()
            add_event(db, job, "Job cancelled", "warning")
            db.commit()
            return

        try:
            video = _upsert_youtube_playlist_video(
                db,
                lesson,
                playlist.playlist_id,
                playlist_video,
            )
            try:
                youtube_transcript = youtube_client.fetch(playlist_video.video_id)
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
                add_event(db, job, f"Processed {playlist_video.title}")
            except YouTubeCaptionUnavailable as exc:
                video.scrape_status = "no_transcript"
                video.scrape_error = f"YouTube captions unavailable: {exc}"
                add_event(db, job, f"No captions for {playlist_video.title}", "warning")
            job.processed_videos += 1
        except Exception as exc:
            logger.exception(
                "YouTube playlist video processing failed: %s",
                playlist_video.video_id,
            )
            video = _upsert_youtube_playlist_video(
                db,
                lesson,
                playlist.playlist_id,
                playlist_video,
            )
            video.scrape_status = "failed"
            video.scrape_error = str(exc)
            job.failed_videos += 1
            add_event(
                db,
                job,
                f"Failed {playlist_video.title}: {exc}",
                "error",
                {"youtube_id": playlist_video.video_id},
            )

        attempted = job.processed_videos + job.failed_videos
        job.progress_percent = (
            round(attempted * 100 / job.total_videos) if job.total_videos else 100
        )
        job.current_step = f"Processed {attempted} of {job.total_videos} videos"
        db.commit()
        if settings.request_delay_seconds and index < len(playlist.videos):
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


def _process_audio_transcript_job(
    db: Session,
    job_id: str,
    transcriber: FasterWhisperTranscriber | None = None,
) -> None:
    transcriber = transcriber or FasterWhisperTranscriber()

    def update_progress(step: str, progress_percent: int) -> None:
        progress_job = db.get(ScrapeJob, job_id)
        if progress_job is None or progress_job.status != JobStatus.running:
            return
        progress_job.current_step = step
        progress_job.progress_percent = max(
            progress_job.progress_percent or 0,
            progress_percent,
        )
        db.commit()

    job = db.get(ScrapeJob, job_id)
    video_id = video_id_from_audio_transcript_path(job.normalized_path)
    video = db.get(Video, video_id)
    if video is None:
        raise AudioTranscriptionUnavailable("Video not found.")

    unit = video.lesson.unit
    job.course_id = unit.course_id
    job.total_videos = 1
    job.current_step = "Generating transcript from audio"
    add_event(db, job, f"Audio transcription started for {video.title}")

    if video.transcript is not None:
        job.processed_videos = 1
        job.progress_percent = 100
        job.status = JobStatus.completed
        job.current_step = "Transcript already exists"
        job.finished_at = now()
        add_event(db, job, "Skipped because the video already has a transcript")
        db.commit()
        return

    if not video.youtube_id:
        raise AudioTranscriptionUnavailable("Only YouTube-backed videos can be transcribed from audio.")

    video.scrape_status = "transcribing"
    video.scrape_error = None
    db.commit()

    try:
        result = transcriber.transcribe_video(
            f"https://www.youtube.com/watch?v={video.youtube_id}",
            progress_callback=update_progress,
        )
    except Exception as exc:
        job = db.get(ScrapeJob, job_id)
        video = db.get(Video, video_id)
        video.scrape_status = "no_transcript"
        video.scrape_error = f"Audio transcription failed: {exc}"
        job.failed_videos = 1
        job.progress_percent = 100
        job.status = JobStatus.failed
        job.current_step = "Failed"
        job.error_message = str(exc)
        job.finished_at = now()
        add_event(db, job, f"Audio transcription failed: {exc}", "error")
        db.commit()
        return

    job = db.get(ScrapeJob, job_id)
    video = db.get(Video, video_id)
    _replace_transcript(
        db,
        video,
        result.plain_text,
        result.segments,
        source="whisper_generated",
        language_code=result.language_code,
    )
    video.scrape_status = "completed"
    video.scrape_error = None
    job.processed_videos = 1
    job.progress_percent = 100
    job.status = JobStatus.completed
    job.current_step = "Finished"
    job.finished_at = now()
    add_event(db, job, f"Generated transcript for {video.title}")
    db.commit()


def process_job(
    job_id: str,
    client: KhanClient | None = None,
    youtube_client: YouTubeCaptionClient | None = None,
    youtube_playlist_client: YouTubePlaylistClient | None = None,
    audio_transcriber: FasterWhisperTranscriber | None = None,
) -> None:
    if youtube_client is None and settings.youtube_fallback_enabled:
        youtube_client = YouTubeCaptionClient(_youtube_languages())
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

        if is_youtube_playlist_path(job.normalized_path):
            _process_youtube_playlist_job(
                db,
                job_id,
                youtube_client,
                youtube_playlist_client,
            )
            return

        if is_audio_transcript_path(job.normalized_path):
            _process_audio_transcript_job(db, job_id, audio_transcriber)
            return

        client = client or KhanClient(settings.khan_country_code, settings.khan_cookie)
        response = client.fetch_course(job.normalized_path)
        payload = client.course_payload(response)
        candidates = client.video_candidates(response)
        if settings.max_videos_per_job is not None:
            candidates = candidates[: settings.max_videos_per_job]

        job = db.get(ScrapeJob, job_id)
        course = _upsert_course(db, payload, job.normalized_path, job.category_id)
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

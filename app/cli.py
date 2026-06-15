import argparse

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal, create_tables
from app.importer import import_transcript_csv
from app.models import Transcript
from app.services.youtube_backfill import backfill_missing_youtube_captions
from app.worker import run_worker


def repair_timestamps() -> tuple[int, int]:
    repaired_transcripts = 0
    repaired_segments = 0
    with SessionLocal() as db:
        transcripts = db.scalars(
            select(Transcript).options(
                selectinload(Transcript.video),
                selectinload(Transcript.segments),
            )
        ).all()
        for transcript in transcripts:
            end_times = [
                segment.end_time_seconds
                for segment in transcript.segments
                if segment.end_time_seconds is not None
            ]
            if not end_times:
                continue
            duration = transcript.video.duration_seconds
            looks_like_milliseconds = (
                max(end_times) > duration * 10
                if duration
                else max(end_times) > 36000
            )
            if not looks_like_milliseconds:
                continue
            for segment in transcript.segments:
                if segment.start_time_seconds is not None:
                    segment.start_time_seconds /= 1000
                if segment.end_time_seconds is not None:
                    segment.end_time_seconds /= 1000
                repaired_segments += 1
            repaired_transcripts += 1
        db.commit()
    return repaired_transcripts, repaired_segments


def main() -> None:
    parser = argparse.ArgumentParser(description="Khan transcript application tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import-csv", help="Import an existing scraper CSV"
    )
    import_parser.add_argument("path")
    import_parser.add_argument("--title", default="Grammar")
    import_parser.add_argument(
        "--course-url",
        default="https://www.khanacademy.org/humanities/grammar",
    )
    subparsers.add_parser("worker-once", help="Process queued jobs and exit when idle")
    subparsers.add_parser(
        "repair-timestamps",
        help="Convert previously stored Khan subtitle milliseconds to seconds",
    )
    backfill_parser = subparsers.add_parser(
        "backfill-youtube",
        help="Fetch YouTube captions for stored videos without transcripts",
    )
    backfill_parser.add_argument("course_id")
    backfill_parser.add_argument("--limit", type=int)

    args = parser.parse_args()
    create_tables()

    if args.command == "import-csv":
        with SessionLocal() as db:
            course, count = import_transcript_csv(
                db,
                args.path,
                course_title=args.title,
                course_url=args.course_url,
            )
        print(f"Imported {count} transcripts into course {course.id}")
    elif args.command == "worker-once":
        run_worker(once=True)
    elif args.command == "repair-timestamps":
        transcripts, segments = repair_timestamps()
        print(f"Repaired {segments} segments across {transcripts} transcripts")
    elif args.command == "backfill-youtube":
        result = backfill_missing_youtube_captions(args.course_id, limit=args.limit)
        print(
            f"Fetched {result['fetched']} captions; "
            f"{result['unavailable']} unavailable; "
            f"{result['skipped']} skipped"
        )


if __name__ == "__main__":
    main()

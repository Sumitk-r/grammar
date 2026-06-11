import argparse

from app.database import SessionLocal, create_tables
from app.importer import import_transcript_csv
from app.worker import run_worker


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


if __name__ == "__main__":
    main()


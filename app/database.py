from collections.abc import Generator
import logging

from sqlalchemy import create_engine, inspect, or_, select, text, update
from sqlalchemy.orm import DeclarativeBase, Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine_options = {}
if settings.database_url in {"sqlite://", "sqlite:///:memory:"}:
    engine_options["poolclass"] = StaticPool
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    **engine_options,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_tables() -> None:
    from app import models  # noqa: F401
    from app.models import Category, Course, ScrapeJob

    Base.metadata.create_all(bind=engine)
    ensure_category_schema()
    ensure_transcript_embedding_schema()
    ensure_pgvector_schema()

    with SessionLocal() as db:
        category = db.scalar(
            select(Category).where(Category.slug == "uncategorized")
        )
        if category is None:
            category = Category(name="Uncategorized", slug="uncategorized")
            db.add(category)
            db.flush()
        db.execute(
            update(Course)
            .where(Course.category_id.is_(None))
            .values(category_id=category.id)
        )
        db.execute(
            update(ScrapeJob)
            .where(ScrapeJob.category_id.is_(None))
            .values(category_id=category.id)
        )
        db.commit()
        try:
            from app.models import Transcript, TranscriptEmbedding
            from app.services.pgvector_search import (
                backfill_pgvector_embeddings,
                sync_pgvector_embeddings,
            )
            from app.services.transcript_chunks import transcript_chunk_rows

            transcripts_to_rebuild = db.scalars(
                select(Transcript)
                .join(TranscriptEmbedding)
                .where(
                    or_(
                        TranscriptEmbedding.video_id.is_(None),
                        TranscriptEmbedding.chunk_metadata.is_(None),
                    )
                )
                .options(
                    selectinload(Transcript.video),
                    selectinload(Transcript.segments),
                    selectinload(Transcript.embeddings),
                )
                .distinct()
            ).all()
            for transcript in transcripts_to_rebuild:
                segments = [
                    {
                        "segment_index": segment.segment_index,
                        "start_time_seconds": segment.start_time_seconds,
                        "end_time_seconds": segment.end_time_seconds,
                        "text": segment.text,
                    }
                    for segment in transcript.segments
                ]
                transcript.embeddings.clear()
                db.flush()
                rebuilt_chunks = transcript_chunk_rows(
                    transcript,
                    transcript.video,
                    transcript.plain_text,
                    segments,
                )
                for chunk in rebuilt_chunks:
                    transcript.embeddings.append(chunk)
                db.flush()
                sync_pgvector_embeddings(db, rebuilt_chunks)

            backfill_pgvector_embeddings(db)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("pgvector embedding backfill skipped: %s", exc)


def ensure_category_schema() -> None:
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name in ("courses", "scrape_jobs"):
            if table_name not in inspector.get_table_names():
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "category_id" not in columns:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN category_id VARCHAR(36)")
                )
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table_name}_category_id "
                    f"ON {table_name} (category_id)"
                )
            )


def ensure_transcript_embedding_schema() -> None:
    inspector = inspect(engine)
    if "transcript_embeddings" not in inspector.get_table_names():
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("transcript_embeddings")
    }
    column_types = {
        "video_id": "VARCHAR(36)",
        "video_title": "VARCHAR(500)",
        "source_url": "TEXT",
        "start_time_seconds": "DOUBLE PRECISION",
        "end_time_seconds": "DOUBLE PRECISION",
        "chunk_metadata": "JSON",
    }
    if engine.dialect.name == "sqlite":
        column_types["start_time_seconds"] = "FLOAT"
        column_types["end_time_seconds"] = "FLOAT"

    with engine.begin() as connection:
        for column_name, column_type in column_types.items():
            if column_name not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE transcript_embeddings "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        if engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    "UPDATE transcript_embeddings te "
                    "SET video_id = v.id, "
                    "video_title = v.title, "
                    "source_url = v.full_url "
                    "FROM transcripts t "
                    "JOIN videos v ON v.id = t.video_id "
                    "WHERE te.transcript_id = t.id "
                    "AND te.video_id IS NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_transcript_embeddings_video_id "
                    "ON transcript_embeddings (video_id)"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE transcript_embeddings "
                    "ADD COLUMN IF NOT EXISTS search_vector tsvector"
                )
            )
            connection.execute(
                text(
                    "UPDATE transcript_embeddings "
                    "SET search_vector = to_tsvector("
                    "'english', concat_ws(' ', video_title, text)"
                    ") "
                    "WHERE search_vector IS NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_transcript_embeddings_search_vector_gin "
                    "ON transcript_embeddings USING gin (search_vector)"
                )
            )


def ensure_pgvector_schema() -> None:
    if engine.dialect.name != "postgresql" or not settings.pgvector_enabled:
        return

    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(
                text(
                    "ALTER TABLE transcript_embeddings "
                    f"ADD COLUMN IF NOT EXISTS embedding vector({settings.embedding_dimensions})"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_transcript_embeddings_embedding_hnsw "
                    "ON transcript_embeddings "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )
    except Exception as exc:
        logger.warning("pgvector schema setup skipped: %s", exc)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

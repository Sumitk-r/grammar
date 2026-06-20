from collections.abc import Generator
import logging

from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
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
            from app.services.pgvector_search import backfill_pgvector_embeddings

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


def ensure_pgvector_schema() -> None:
    if engine.dialect.name != "postgresql":
        return

    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(
                text(
                    "ALTER TABLE transcript_embeddings "
                    "ADD COLUMN IF NOT EXISTS embedding vector(128)"
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

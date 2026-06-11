# Khan Transcript Library

A database-backed web application for scraping, browsing, searching, and
exporting Khan Academy course transcripts. It includes:

- FastAPI API and server-rendered responsive UI
- PostgreSQL or SQLite storage through SQLAlchemy
- Separate database-backed background worker
- Idempotent course, unit, lesson, video, and transcript upserts
- Job progress, event history, cancellation, and per-video failures
- Transcript search and CSV/JSON exports
- Import support for the existing scraper CSV

## Quick Start

Create a virtual environment and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Import the included 123 Grammar transcripts:

```powershell
python -m app.cli import-csv khan_grammar_transcripts.csv
```

Start the API and worker in separate terminals:

```powershell
uvicorn app.main:app --reload
```

```powershell
python -m app.worker
```

Open `http://localhost:8000`. API documentation is available at
`http://localhost:8000/docs`.

SQLite is the default, so no database setup is required. To use PostgreSQL,
set `DATABASE_URL`:

```text
DATABASE_URL=postgresql+psycopg://user:password@localhost/grammar
```

## Docker

Run the API, worker, and PostgreSQL together:

```powershell
docker compose up --build
```

Then import the included CSV into PostgreSQL:

```powershell
docker compose exec api python -m app.cli import-csv khan_grammar_transcripts.csv
```

## Configuration

Copy `.env.example` to `.env` and adjust values as needed.

- `DATABASE_URL`: SQLAlchemy database URL
- `KHAN_COUNTRY_CODE`: country code for Khan Academy GraphQL requests
- `KHAN_COOKIE`: optional admin-provided Cookie header when a client challenge occurs
- `REQUEST_DELAY_SECONDS`: delay between video requests
- `WORKER_POLL_SECONDS`: idle worker polling interval
- `MAX_VIDEOS_PER_JOB`: optional cap useful during development

Cookies are read only from server configuration and are never accepted through
the public API or shown in the UI.

## API

Core endpoints:

- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/cancel`
- `GET /api/jobs/{job_id}/course`
- `GET /api/courses/{course_id}`
- `GET /api/search?course_id=...&q=...`
- `GET /api/videos/{video_id}/transcript`
- `GET /api/courses/{course_id}/export.csv`
- `GET /api/courses/{course_id}/export.json`

## Tests

```powershell
pytest
```

The original `scrape_khan_grammar.py` remains available as a standalone CSV
scraper. The web worker imports its current Khan Academy GraphQL operations and
parsing helpers so both workflows use the same integration logic.


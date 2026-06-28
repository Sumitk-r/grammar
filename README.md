# Aveti Transcripts

A database-backed web application for scraping, browsing, searching, and
exporting transcripts from Khan Academy courses and YouTube playlists. It includes:

- FastAPI API and server-rendered responsive UI
- PostgreSQL or SQLite storage through SQLAlchemy
- Separate database-backed background worker
- Idempotent course, unit, lesson, video, and transcript upserts
- Job progress, event history, cancellation, and per-video failures
- Hybrid transcript search, timestamped results, and CSV/JSON exports
- Dashboard global search across all categories, courses, and playlists
- Admin job dashboard with retry controls
- Import support for the existing scraper CSV
- YouTube playlist support using available manual or auto-generated captions
- Admin-triggered faster-whisper audio transcription for YouTube videos without captions

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

On Windows, you can start both required processes together:

```powershell
.\run-local.ps1
```

Or start the API and worker in separate terminals:

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

Hybrid search uses PostgreSQL full-text search plus pgvector when PostgreSQL
has the `vector` extension installed. When pgvector is not available, the app
uses local hybrid search over stored JSON embeddings plus keyword/fuzzy ranking,
so search remains useful in local development and on smaller VMs.

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
- `YOUTUBE_FALLBACK_ENABLED`: try YouTube captions when Khan subtitles are absent
- `YOUTUBE_LANGUAGES`: preferred YouTube caption language codes
- `YT_DLP_COOKIES_FILE`: optional path to an exported YouTube cookies.txt file for VMs that hit bot checks
- `AUDIO_TRANSCRIPTION_ENABLED`: allow admin-triggered audio transcription jobs
- `AUDIO_TRANSCRIPTION_MAX_DURATION_SECONDS`: max video length for audio transcription
- `FASTER_WHISPER_MODEL`: faster-whisper model name, defaults to `tiny`
- `FASTER_WHISPER_DEVICE`: faster-whisper device, defaults to `cpu`
- `FASTER_WHISPER_COMPUTE_TYPE`: faster-whisper compute type, defaults to `int8`
- `EMBEDDING_DIMENSIONS`: vector dimensions used for free local embeddings
- `PGVECTOR_ENABLED`: set to `true` only after the Postgres pgvector extension is installed
- `DISPLAY_TIMEZONE`: timezone used for timestamps in the web UI
- `ADMIN_KEY`: admin key required to create categories

Cookies are read only from server configuration and are never accepted through
the public API or shown in the UI.

The YouTube caption integration supports manual and auto-generated captions. It uses an
undocumented YouTube web endpoint, so it may stop working when YouTube changes
its internals. YouTube also frequently blocks cloud-provider IP addresses; a
local worker is generally more reliable than an Azure-hosted worker for this
caption fetching.

If a VM shows "Sign in to confirm you're not a bot" from YouTube, export
YouTube cookies from a browser into a Netscape-format `cookies.txt`, copy it to
the VM, set `YT_DLP_COOKIES_FILE=/home/ubuntu/grammar/cookies.txt`, and restart
the API and worker. Keep this file private.

When YouTube captions are unavailable, an admin can open the no-transcript video
page and queue "Generate transcript". The worker downloads audio with `yt-dlp`,
transcribes it with faster-whisper, stores the generated transcript and
timestamps in the database, and all users then read the saved transcript. Install
`ffmpeg` on the host before using this feature.

Search results are grouped by video and include matching transcript timestamps.
Opening a timestamped result jumps to the closest transcript segment on the
video page.

Production deployment helpers are in `deploy/`:

- `deploy/grammar-api.service`
- `deploy/grammar-worker.service`
- `deploy/nginx-aveti.conf`
- `.env.production.example`

## API

Core endpoints:

- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/cancel`
- `POST /api/jobs/{job_id}/retry`
- `GET /api/jobs/{job_id}/course`
- `GET /api/courses/{course_id}`
- `GET /api/search?course_id=...&q=...`
- `GET /api/system/search-status`
- `GET /api/videos/{video_id}/transcript`
- `POST /api/videos/{video_id}/generate-transcript`
- `POST /api/courses/{course_id}/generate-missing-transcripts`
- `GET /api/courses/{course_id}/export.csv`
- `GET /api/courses/{course_id}/export.json`

## Tests

```powershell
pytest
```

The original `scrape_khan_grammar.py` remains available as a standalone CSV
scraper. The web worker imports its current Khan Academy GraphQL operations and
parsing helpers so both workflows use the same integration logic.

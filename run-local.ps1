$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Create it first, then run: .\.venv\Scripts\pip install -r requirements.txt"
}

Write-Host "Starting Aveti API on http://127.0.0.1:8000"
Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $projectRoot

Write-Host "Starting Aveti worker for scraping and transcript jobs"
Start-Process -FilePath $python `
    -ArgumentList @("-m", "app.worker") `
    -WorkingDirectory $projectRoot

Write-Host ""
Write-Host "Both processes were started. Keep the worker running while scraping or generating transcripts."
Write-Host "Open http://127.0.0.1:8000/"

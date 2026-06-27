param(
    [string]$PgRoot = "C:\Program Files\PostgreSQL\18",
    [string]$Database = "grammar",
    [string]$User = "postgres",
    [string]$Tag = "v0.8.3"
)

$ErrorActionPreference = "Stop"

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. Run this from 'x64 Native Tools Command Prompt for VS' as Administrator."
    }
}

if (-not (Test-Path "$PgRoot\bin\pg_config.exe")) {
    throw "PostgreSQL pg_config.exe was not found at $PgRoot\bin\pg_config.exe"
}

Require-Command git
Require-Command nmake
Require-Command cl

$env:PGROOT = $PgRoot
$workDir = Join-Path $env:TEMP "pgvector-build"
if (Test-Path $workDir) {
    Remove-Item -LiteralPath $workDir -Recurse -Force
}
New-Item -ItemType Directory -Path $workDir | Out-Null

Push-Location $workDir
try {
    git clone --branch $Tag https://github.com/pgvector/pgvector.git
    Push-Location pgvector
    try {
        nmake /F Makefile.win
        nmake /F Makefile.win install
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}

& "$PgRoot\bin\psql.exe" -U $User -d $Database -c "CREATE EXTENSION IF NOT EXISTS vector;"
& "$PgRoot\bin\psql.exe" -U $User -d $Database -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"

Write-Host "pgvector install finished. Restart the Aveti app, then open /api/system/search-status."

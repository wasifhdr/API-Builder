# Starts the dev stack: FastAPI (reload), the worker (once it exists), and Vite.
# Each runs in its own PowerShell window so logs stay readable.
# Postgres/Redis (docker compose up -d) are started separately since they're
# infrequently restarted.

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

Write-Host "Reminder: run 'docker compose up -d' for Postgres+Redis (the app works with LLM_ENABLED=false if no LLM key is configured)." -ForegroundColor Yellow

# -WorkingDirectory (not an embedded `cd "$path";`) because Start-Process's
# -ArgumentList array mangles elements that mix embedded quotes with a `;` —
# it silently drops the quotes, which breaks paths containing spaces.
Start-Process powershell -ArgumentList "-NoExit", "-Command", "uv run uvicorn app.main:app --reload --port 8000" -WorkingDirectory $backend

$workerMain = Join-Path $backend "app\workers\main.py"
if (Test-Path $workerMain) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "uv run python -m app.workers.main" -WorkingDirectory $backend
} else {
    Write-Host "Skipping worker: backend/app/workers/main.py doesn't exist yet (added in Phase 3)." -ForegroundColor DarkGray
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", "npm run dev" -WorkingDirectory $frontend

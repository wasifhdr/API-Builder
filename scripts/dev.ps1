# Starts the dev stack: FastAPI (reload), the worker (once it exists), and Vite.
# Each runs in its own PowerShell window so logs stay readable.
# Postgres/Redis (docker compose up -d) and llama-server (run-llama.ps1) are
# started separately since they're optional/infrequently restarted.

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

Write-Host "Reminder: run 'docker compose up -d' for Postgres+Redis, and scripts\run-llama.ps1 for the local LLM (optional; app works with LLM_ENABLED=false)." -ForegroundColor Yellow

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$backend`"; uv run uvicorn app.main:app --reload --port 8000"

$workerMain = Join-Path $backend "app\workers\main.py"
if (Test-Path $workerMain) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$backend`"; uv run python -m app.workers.main"
} else {
    Write-Host "Skipping worker: backend/app/workers/main.py doesn't exist yet (added in Phase 3)." -ForegroundColor DarkGray
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$frontend`"; npm run dev"

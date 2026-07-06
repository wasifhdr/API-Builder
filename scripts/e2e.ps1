# End-to-end smoke test: records a workflow against the local fixture site
# via the real WS protocol, publishes it, executes it through /v1/run, and
# asserts the extracted JSON. Drives the real running system — start
# Postgres/Redis (docker compose up -d), the backend, and the worker first
# (scripts\dev.ps1), then run this.

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

Push-Location $backend
try {
    uv run python -m scripts.e2e_smoke
    if ($LASTEXITCODE -ne 0) {
        Write-Host "E2E smoke test FAILED" -ForegroundColor Red
        exit 1
    }
    Write-Host "E2E smoke test PASSED" -ForegroundColor Green
} finally {
    Pop-Location
}

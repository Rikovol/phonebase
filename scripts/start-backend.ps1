# Запуск API из каталога backend (нужен Python 3.11+ и venv с зависимостями).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")
if (-not (Test-Path ".\.venv\Scripts\uvicorn.exe")) {
    Write-Host "Создаю venv и ставлю зависимости..."
    python -m venv .venv
    & ".\.venv\Scripts\pip.exe" install -r requirements.txt
}
& ".\.venv\Scripts\uvicorn.exe" app.main:app --reload --host 127.0.0.1 --port 8000

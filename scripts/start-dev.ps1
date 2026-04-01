# Два окна: API (8000) и фронт (5173). Закройте окна для остановки.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$nodeDir = "${env:ProgramFiles}\nodejs"
$pathPrefix = if (Test-Path (Join-Path $nodeDir "node.exe")) { "$nodeDir;" } else { "" }
if (-not $pathPrefix) {
    Write-Warning "Не найден $nodeDir\node.exe — установите Node.js LTS или добавьте node в PATH."
}

$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }

function Test-PortListen([int]$Port) {
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return $null -ne $c
    } catch {
        return $false
    }
}
if (Test-PortListen 8000) {
    Write-Warning "Порт 8000 уже занят — закройте другой uvicorn/Python или освободите порт."
}
if (Test-PortListen 5173) {
    Write-Warning "Порт 5173 уже занят — закройте старый Vite (окно npm run dev) или освободите порт."
}

$backendCmd = @"
Set-Location '$root\backend'
if (-not (Test-Path '.\.venv\Scripts\uvicorn.exe')) { python -m venv .venv; & '.\.venv\Scripts\pip.exe' install -r requirements.txt }
& '.\.venv\Scripts\uvicorn.exe' app.main:app --reload --host 127.0.0.1 --port 8000
"@

$frontendCmd = @"
`${env:Path} = '$pathPrefix' + `${env:Path}
Set-Location '$root\frontend'
if (-not (Test-Path '.\node_modules\vite')) { npm install }
npm run dev
"@

Start-Process $shell -WorkingDirectory $root -ArgumentList @("-NoExit", "-Command", $backendCmd)
Start-Process $shell -WorkingDirectory $root -ArgumentList @("-NoExit", "-Command", $frontendCmd)
Write-Host ""
Write-Host "Откройте в браузере: http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "(API: http://127.0.0.1:8000 — фронт проксирует /api и /media)" -ForegroundColor DarkGray

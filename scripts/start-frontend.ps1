# Запуск Vite (интерфейс). Node должен быть в PATH; иначе подхватываем стандартную установку.
$ErrorActionPreference = "Stop"
$nodeDir = "${env:ProgramFiles}\nodejs"
if (Test-Path (Join-Path $nodeDir "node.exe")) {
    $env:Path = "$nodeDir;$env:Path"
}
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "frontend")
if (-not (Test-Path ".\node_modules\vite")) {
    Write-Host "Ставлю зависимости npm..."
    & npm install
}
& npm run dev -- --host 127.0.0.1 --port 5173

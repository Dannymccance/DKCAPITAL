$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

Write-Host "Checking Python 3.11..."
& py -3.11 -c "import sys; print(sys.version)"

if (-not (Test-Path ".venv")) {
    & py -3.11 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created windows/.env. Add the gateway token and any MT5 login settings before running."
}

New-Item -ItemType Directory -Force -Path "data" | Out-Null
Write-Host "MT5 shadow executor installed. No live-order execution code is included."

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
Set-Location $Root

$env:PYTHONPATH = Join-Path $Root "src"
& (Join-Path $Here ".venv\Scripts\python.exe") (Join-Path $Here "mt5_shadow_executor.py")

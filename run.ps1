# Signal Desk launcher
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host "Checking dependencies..." -ForegroundColor DarkGray
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

Write-Host "Signal Desk -> http://127.0.0.1:8000" -ForegroundColor Green
python -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000

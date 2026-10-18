$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
}
& '.\.venv\Scripts\python.exe' -m ensurepip --upgrade
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
Write-Host 'Setup complete. Run .\run.ps1 and open http://127.0.0.1:8765/'

$ErrorActionPreference = "Stop"

py -3 -m venv .venv

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host ""
Write-Host "Installation complete."
Write-Host "Run with:"
Write-Host "  .\run_windows.ps1"
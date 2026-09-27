$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .\config\settings.json)) { Copy-Item .\config\settings.example.json .\config\settings.json }
Write-Host "Installed. Run tests next."

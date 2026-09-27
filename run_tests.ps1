$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
.\.venv\Scripts\python.exe .\dependency_smoke.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\self_check.py

@echo off
rem Writes logs\scorecard.html from the run logs and opens it. Same as: .venv\Scripts\python.exe scorecard.py
cd /d "%~dp0"
".venv\Scripts\python.exe" scorecard.py

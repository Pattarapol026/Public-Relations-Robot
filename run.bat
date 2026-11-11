@echo off
cd /d "%~dp0"
".venv313\Scripts\python.exe" main.py
if errorlevel 1 pause

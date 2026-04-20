@echo off
REM Phase 48 — pytest runner
REM Run unit tests on Windows. Install once: py -3.11 -m pip install pytest
cd /d "%~dp0"
py -3.11 -m pytest tests/unit -v --tb=short
pause

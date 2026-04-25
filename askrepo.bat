@echo off
rem askrepo.bat
rem -----------
rem Windows launcher for AskRepo.
rem Uses the local .venv Python automatically — no manual activation needed.
rem
rem Usage:
rem   askrepo index <path>
rem   askrepo index-repo <owner/repo>
rem   askrepo query "<question>"
rem   askrepo list
rem   askrepo clear
rem   askrepo count

"%~dp0.venv\Scripts\python.exe" "%~dp0main.py" %*

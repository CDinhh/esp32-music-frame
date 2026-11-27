@echo off
rem Shows the song playing on this PC (and its lyrics) on the screen. Close this window to stop.
title Music Frame
cd /d "%~dp0pc"
".venv\Scripts\python.exe" -m musicframe.main
pause

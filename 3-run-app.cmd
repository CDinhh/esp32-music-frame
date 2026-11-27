@echo off
rem Shows the song playing on this PC (and its lyrics) on the screen. Close this window to stop.
title Music Frame
cd /d "%~dp0pc"
powershell -NoProfile -Command "$me=$PID; $myCmd=(Get-CimInstance Win32_Process -Filter \"ProcessId=$me\" -ErrorAction SilentlyContinue).ParentProcessId; Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { ($_.Name -like 'python*.exe' -and $_.CommandLine -match 'musicframe\.main') -or ($_.Name -eq 'cmd.exe' -and $_.ProcessId -ne $myCmd -and $_.CommandLine -like '*3-run-app.cmd*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
".venv\Scripts\python.exe" -m musicframe.main
pause

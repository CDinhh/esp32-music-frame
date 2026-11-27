@echo off
rem Build the firmware and send it to the screen over WiFi. Double-click me.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\firmware.ps1" -Action wifi
pause

@echo off
rem Build the firmware and flash it over a USB cable. Double-click me.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\firmware.ps1" -Action usb
pause

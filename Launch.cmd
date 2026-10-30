@echo off
title Canvas DDL Reminder
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
echo.
echo Canvas DDL Reminder has stopped. Keep this window open while using reminders.
pause

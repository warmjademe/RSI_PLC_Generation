@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Inspect-Win11Template.ps1" >"%~dp0template-diagnostic-run.log" 2>&1
set diagnostic_exit=%errorlevel%
(echo %diagnostic_exit%) >"%~dp0template-diagnostic-exit.txt"

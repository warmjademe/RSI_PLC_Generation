@echo off
fltmc.exe >nul 2>&1
if errorlevel 1 (
    powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-DeltaTools.ps1" >"%~dp0delta-install-run.log" 2>&1
set install_exit=%errorlevel%
(echo %install_exit%) >"%~dp0delta-install-exit.txt"

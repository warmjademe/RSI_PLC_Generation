@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Validate-DeltaTools.ps1" >"%~dp0delta-runtime-validation.log" 2>&1
set validation_exit=%errorlevel%
(echo %validation_exit%) >"%~dp0delta-runtime-validation-exit.txt"


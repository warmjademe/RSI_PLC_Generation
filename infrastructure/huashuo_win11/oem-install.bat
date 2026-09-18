@echo off
setlocal

for /L %%I in (1,1,60) do (
    if exist "\\host.lan\Data\installers\ISPSoft3.24.exe" goto installers_ready
    timeout /t 10 /nobreak >nul
)

echo Timed out waiting for the host shared folder.
exit /b 2

:installers_ready
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\OEM\Install-DeltaTools.ps1"
exit /b %errorlevel%


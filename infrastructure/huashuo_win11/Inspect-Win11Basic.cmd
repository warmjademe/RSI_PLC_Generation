@echo off
(
    echo === TIME ===
    wmic.exe os get LocalDateTime,Caption,Version,BuildNumber /value
    echo === IDENTITY ===
    hostname.exe
    whoami.exe
    echo === PROCESSES ===
    tasklist.exe /v
    echo === OEM FILES ===
    dir.exe /a "C:\OEM"
    echo === OEM INSTALL LOG ===
    if exist "C:\OEM\install.log" type "C:\OEM\install.log"
    echo === ISPSOFT EXPECTED PATH ===
    dir.exe /a "C:\Program Files (x86)\Delta Industrial Automation\ISPSoft 3.24\NewISPSoft.exe"
    echo === DELTA UNINSTALL KEYS ===
    reg.exe query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" /s /f "Delta" /d
    echo === IDENTITY TASK ===
    schtasks.exe /query /tn "DeltaPLC-ConfigureTerminalIdentity" /v /fo list
) >"%~dp0template-basic-diagnostic.log" 2>&1


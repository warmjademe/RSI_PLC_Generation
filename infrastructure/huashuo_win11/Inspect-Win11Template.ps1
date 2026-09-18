[CmdletBinding()]
param(
    [string]$OutputPath = '\\host.lan\Data\template-diagnostic.json'
)

$ErrorActionPreference = 'Stop'

$uninstallRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$interestingProcesses = 'setup|install|msiexec|ispsoft|newispsoft|commgr|diacom|powershell|cmd'
$oemLogPath = 'C:\OEM\install.log'
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)

$diagnostic = [ordered]@{
    schema_version = 1
    checked_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    computer_name = $env:COMPUTERNAME
    user_name = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    is_elevated = $currentPrincipal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    oem_files = @(
        Get-ChildItem 'C:\OEM' -File -ErrorAction SilentlyContinue |
            Select-Object Name, Length, LastWriteTimeUtc
    )
    oem_log_tail = if (Test-Path -LiteralPath $oemLogPath) {
        @(Get-Content -LiteralPath $oemLogPath -Tail 120 -ErrorAction SilentlyContinue)
    } else {
        @()
    }
    processes = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessName -match $interestingProcesses } |
            Select-Object ProcessName, Id, StartTime, Path
    )
    delta_products = @(
        Get-ItemProperty -Path $uninstallRoots -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'ISPSoft|COMMGR|Delta Industrial' } |
            Select-Object DisplayName, DisplayVersion, InstallLocation, Publisher
    )
    ispsoft_exists = Test-Path -LiteralPath 'C:\Program Files (x86)\Delta Industrial Automation\ISPSoft 3.24\NewISPSoft.exe'
    install_report_exists = Test-Path -LiteralPath '\\host.lan\Data\delta-install-report.json'
    identity_task = @(
        Get-ScheduledTask -TaskName 'DeltaPLC-ConfigureTerminalIdentity' -ErrorAction SilentlyContinue |
            Select-Object TaskName, State
    )
}

$diagnostic | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding UTF8

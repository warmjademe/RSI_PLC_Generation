[CmdletBinding()]
param(
    [string]$OutputPath = '\\host.lan\Data\delta-runtime-validation.json',
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = 'Stop'
$ispSoftPath = 'C:\Program Files (x86)\Delta Industrial Automation\ISPSoft 3.24\NewISPSoft.exe'
$commgrPath = 'C:\Program Files (x86)\Delta Industrial Automation\DIAStudio\DIATools\COMMGR.exe'
$uninstallRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)

function Test-ApplicationLaunch {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ProcessName
    )

    $beforeIds = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | ForEach-Object Id)
    $result = [ordered]@{
        name = $Name
        path = $Path
        exists = Test-Path -LiteralPath $Path -PathType Leaf
        product_version = $null
        launch_observed = $false
        processes = @()
        error = $null
    }

    try {
        if (-not $result.exists) {
            throw "Executable not found: $Path"
        }
        $result.product_version = (Get-Item -LiteralPath $Path).VersionInfo.ProductVersion
        Start-Process -FilePath $Path | Out-Null

        $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        do {
            Start-Sleep -Seconds 2
            $processes = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
        } while ($processes.Count -eq 0 -and (Get-Date) -lt $deadline)

        $result.processes = @(
            $processes | ForEach-Object {
                [ordered]@{
                    id = $_.Id
                    responding = $_.Responding
                    main_window_title = $_.MainWindowTitle
                }
            }
        )
        $result.launch_observed = $processes.Count -gt 0
        if (-not $result.launch_observed) {
            throw "$Name did not create a $ProcessName process within $StartupTimeoutSeconds seconds."
        }
    }
    catch {
        $result.error = $_.Exception.Message
    }
    finally {
        $newProcesses = @(
            Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
                Where-Object { $beforeIds -notcontains $_.Id }
        )
        foreach ($process in $newProcesses) {
            $null = $process.CloseMainWindow()
        }
        Start-Sleep -Seconds 5
        $newProcesses | Where-Object { -not $_.HasExited } |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }

    return $result
}

$report = [ordered]@{
    schema_version = 1
    checked_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    checked_at_local = (Get-Date).ToString('o')
    computer_name = $env:COMPUTERNAME
    windows_build = (Get-CimInstance Win32_OperatingSystem).BuildNumber
    time_zone = (Get-TimeZone).Id
    products = @(
        Get-ItemProperty -Path $uninstallRoots -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'ISPSoft|COMMGR' } |
            Sort-Object DisplayName |
            Select-Object DisplayName, DisplayVersion, InstallLocation, Publisher
    )
    applications = @()
    identity_task = @(
        Get-ScheduledTask -TaskName 'DeltaPLC-ConfigureTerminalIdentity' -ErrorAction SilentlyContinue |
            Select-Object TaskName, State
    )
    status = 'running'
}

$report.applications += Test-ApplicationLaunch `
    -Name 'ISPSoft' -Path $ispSoftPath -ProcessName 'NewISPSoft'
$report.applications += Test-ApplicationLaunch `
    -Name 'COMMGR' -Path $commgrPath -ProcessName 'COMMGR'

$requiredProducts = @($report.products | ForEach-Object DisplayName)
$failedApplications = @($report.applications | Where-Object { -not $_.launch_observed })
if (($requiredProducts -match '^ISPSoft 3\.24$') -and
    ($requiredProducts -match '^COMMGR 2\.11\.0\.14$') -and
    $failedApplications.Count -eq 0 -and
    $report.identity_task.Count -gt 0) {
    $report.status = 'ok'
} else {
    $report.status = 'failed'
}

$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
if ($report.status -ne 'ok') {
    exit 1
}

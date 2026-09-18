[CmdletBinding()]
param(
    [string]$SharedRoot = '\\host.lan\Data'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$sourceInstallerDirectory = Join-Path $SharedRoot 'installers'
$localInstallerDirectory = 'C:\DeltaPLCInstall'
$reportPath = Join-Path $SharedRoot 'delta-install-report.json'
$ispSoftSource = Join-Path $sourceInstallerDirectory 'ISPSoft3.24.exe'
$commgrSource = Join-Path $sourceInstallerDirectory 'COMMGR_V2.11.0.exe'
$ispSoftInstaller = Join-Path $localInstallerDirectory 'ISPSoft3.24.exe'
$commgrInstaller = Join-Path $localInstallerDirectory 'COMMGR_V2.11.0.exe'

$expectedHashes = @(
    [ordered]@{
        path = $ispSoftInstaller
        sha256 = '5d1fc0ac313ae16fc94310aa693e76a4031a90c0f15de8ef6a86141523e75dbc'
    },
    [ordered]@{
        path = $commgrInstaller
        sha256 = '232ef16f61a7653fbe4ca8f7d8567d3f1ee9e697b1a56eb805b4de56e63b24ee'
    }
)

$report = [ordered]@{
    schema_version = 1
    started_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    computer_name = $env:COMPUTERNAME
    windows = [ordered]@{
        caption = $null
        version = $null
        build = $null
    }
    installers = @()
    products = @()
    executables = @()
    restart_required = $false
    status = 'running'
    error = $null
}

function Add-ExecutableEvidence {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }

    $item = Get-Item -LiteralPath $Path
    $report.executables += [ordered]@{
        path = $item.FullName
        file_version = $item.VersionInfo.FileVersion
        product_version = $item.VersionInfo.ProductVersion
        sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Assert-InstallerHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing installer: $Path"
    }

    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected) {
        throw "SHA256 mismatch for $Path"
    }

    $report.installers += [ordered]@{
        name = Split-Path -Leaf $Path
        sha256 = $actual
        verified = $true
        exit_code = $null
    }
}

function Copy-InstallerToLocalDisk {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Missing source installer: $Source"
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    Unblock-File -LiteralPath $Destination -ErrorAction SilentlyContinue
}

function Invoke-CheckedInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $entry = $report.installers | Where-Object { $_.name -eq (Split-Path -Leaf $Path) } | Select-Object -First 1
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -Wait -PassThru
    $entry.exit_code = $process.ExitCode

    if ($process.ExitCode -eq 3010) {
        $report.restart_required = $true
        return
    }
    if ($process.ExitCode -ne 0) {
        throw "Installer failed with exit code $($process.ExitCode): $Path"
    }
}

try {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentPrincipal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $currentPrincipal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell session.'
    }

    $operatingSystem = Get-CimInstance Win32_OperatingSystem
    $report.windows.caption = $operatingSystem.Caption
    $report.windows.version = $operatingSystem.Version
    $report.windows.build = $operatingSystem.BuildNumber

    New-Item -ItemType Directory -Path $localInstallerDirectory -Force | Out-Null
    Copy-InstallerToLocalDisk -Source $ispSoftSource -Destination $ispSoftInstaller
    Copy-InstallerToLocalDisk -Source $commgrSource -Destination $commgrInstaller

    foreach ($entry in $expectedHashes) {
        Assert-InstallerHash -Path $entry.path -Expected $entry.sha256
    }

    # Run from a local staging directory. Launching either bootstrapper from a
    # UNC share can prevent its embedded MSI payload from being opened.
    # ISPSoft is an NSIS package; COMMGR is an InstallShield launcher wrapping
    # an MSI. Both commands suppress UI and defer any reboot until validation.
    Invoke-CheckedInstaller -Path $ispSoftInstaller -Arguments @('/S')
    Invoke-CheckedInstaller -Path $commgrInstaller -Arguments @('/s', '/v"/qn REBOOT=ReallySuppress"')

    $uninstallRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    $report.products = @(
        Get-ItemProperty -Path $uninstallRoots -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'ISPSoft|COMMGR|Delta Industrial' } |
            Sort-Object DisplayName, DisplayVersion -Unique |
            ForEach-Object {
                [ordered]@{
                    display_name = $_.DisplayName
                    display_version = $_.DisplayVersion
                    install_location = $_.InstallLocation
                    publisher = $_.Publisher
                }
            }
    )

    $ispSoftPath = 'C:\Program Files (x86)\Delta Industrial Automation\ISPSoft 3.24\NewISPSoft.exe'
    Add-ExecutableEvidence -Path $ispSoftPath

    $commgrCandidates = @(
        Get-ChildItem 'C:\Program Files (x86)\Delta Industrial Automation' -Filter '*.exe' -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match 'COMMGR|DIACom' }
    )
    foreach ($candidate in $commgrCandidates) {
        Add-ExecutableEvidence -Path $candidate.FullName
    }

    if (-not (Test-Path -LiteralPath $ispSoftPath -PathType Leaf)) {
        throw "ISPSoft executable was not found at the expected path: $ispSoftPath"
    }
    if (-not ($report.products | Where-Object { $_.display_name -match 'COMMGR' })) {
        throw 'COMMGR was not found in the uninstall registry after installation.'
    }

    $programRoot = 'C:\ProgramData\DeltaPLCValidation'
    New-Item -ItemType Directory -Path $programRoot -Force | Out-Null
    $identityScriptSource = Join-Path $PSScriptRoot 'Configure-TerminalIdentity.ps1'
    $identityScriptTarget = Join-Path $programRoot 'Configure-TerminalIdentity.ps1'
    if (-not (Test-Path -LiteralPath $identityScriptSource -PathType Leaf)) {
        throw "Missing terminal identity script: $identityScriptSource"
    }
    Copy-Item -LiteralPath $identityScriptSource -Destination $identityScriptTarget -Force

    $identityAction = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\DeltaPLCValidation\Configure-TerminalIdentity.ps1"'
    $identityTrigger = New-ScheduledTaskTrigger -AtStartup
    $identityTrigger.Delay = 'PT45S'
    Register-ScheduledTask `
        -TaskName 'DeltaPLC-ConfigureTerminalIdentity' `
        -Action $identityAction `
        -Trigger $identityTrigger `
        -User 'SYSTEM' `
        -RunLevel Highest `
        -Force | Out-Null

    $report.status = 'ok'
}
catch {
    $report.status = 'failed'
    $report.error = $_.Exception.Message
    throw
}
finally {
    $report.finished_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
}

exit 0

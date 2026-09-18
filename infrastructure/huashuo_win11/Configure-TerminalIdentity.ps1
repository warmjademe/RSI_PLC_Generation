[CmdletBinding()]
param(
    [string]$IdentityPath = '\\host.lan\Data\terminal-identity.json'
)

$ErrorActionPreference = 'Stop'
$reportPath = '\\host.lan\Data\terminal-identity-report.json'
$logDirectory = 'C:\ProgramData\DeltaPLCValidation'
$logPath = Join-Path $logDirectory 'terminal-identity.log'

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

for ($attempt = 1; $attempt -le 60; $attempt++) {
    if (Test-Path -LiteralPath $IdentityPath -PathType Leaf) {
        break
    }
    Start-Sleep -Seconds 10
}

try {
    if (-not (Test-Path -LiteralPath $IdentityPath -PathType Leaf)) {
        throw "Identity file did not become available: $IdentityPath"
    }

    $identity = Get-Content -LiteralPath $IdentityPath -Raw | ConvertFrom-Json
    $computerName = [string]$identity.computer_name
    $workerId = [string]$identity.worker_id

    if ($computerName -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,14}$') {
        throw "Invalid Windows computer name: $computerName"
    }
    if ([string]::IsNullOrWhiteSpace($workerId)) {
        throw 'worker_id must not be empty.'
    }

    if ($env:COMPUTERNAME -ne $computerName) {
        "$(Get-Date -Format o) renaming $env:COMPUTERNAME to $computerName" |
            Add-Content -LiteralPath $logPath -Encoding UTF8
        Rename-Computer -NewName $computerName -Force
        Restart-Computer -Force
        exit 0
    }

    $report = [ordered]@{
        schema_version = 1
        checked_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        computer_name = $env:COMPUTERNAME
        worker_id = $workerId
        identity_status = 'ok'
    }
    $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    "$(Get-Date -Format o) identity ready: $workerId on $env:COMPUTERNAME" |
        Add-Content -LiteralPath $logPath -Encoding UTF8
}
catch {
    "$(Get-Date -Format o) identity setup failed: $($_.Exception.Message)" |
        Add-Content -LiteralPath $logPath -Encoding UTF8
    throw
}


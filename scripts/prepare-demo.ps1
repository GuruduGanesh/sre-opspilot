[CmdletBinding()]
param(
    [switch]$ClearIncidentHistory
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DatabasePath = Join-Path $Root "artifacts/opspilot.db"

function Get-LocalOpsPilotApiProcesses {
    $escapedRoot = [regex]::Escape($Root)
    return @(
        Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match $escapedRoot -and
            $_.CommandLine -match 'opspilot\.api\.main|run-console\.ps1'
        }
    )
}

function Stop-LocalOpsPilotApiForReset {
    $processes = Get-LocalOpsPilotApiProcesses
    if ($processes.Count -eq 0) {
        return $false
    }

    $wasControlledSimulation = $processes.CommandLine -match 'run-console\.ps1.*-ControlledSimulation'
    $processes | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline -and (Get-LocalOpsPilotApiProcesses).Count -gt 0) {
        Start-Sleep -Milliseconds 200
    }
    if ((Get-LocalOpsPilotApiProcesses).Count -gt 0) {
        throw "Could not stop the local OpsPilot API process tree. Stop .\scripts\run-console.ps1 manually, then retry."
    }

    Write-Host "Stopped the local OpsPilot API so retained incident history can be cleared."
    return [bool]$wasControlledSimulation
}

function Start-LocalOpsPilotApi {
    param([bool]$ControlledSimulation)

    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '.\scripts\run-console.ps1')
    if ($ControlledSimulation) {
        $arguments += '-ControlledSimulation'
    }

    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    Write-Host "Restarted the local OpsPilot API in the background (process $($process.Id))."
}

function Assert-IncidentHistoryCanBeCleared {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $stream.Dispose()
    }
    catch {
        throw "Cannot clear retained local incident history at $Path after stopping the local API. Close any other process using this file, then retry. No scenario reset was applied by this attempt."
    }
}

Push-Location $Root
try {
    $restartApi = $false
    $restartControlledSimulation = $false
    if ($ClearIncidentHistory) {
        $restartControlledSimulation = Stop-LocalOpsPilotApiForReset
        $restartApi = $true
        Assert-IncidentHistoryCanBeCleared -Path $DatabasePath
    }

    .\scripts\scenario.ps1 reset-p1
    .\scripts\scenario.ps1 reset-p2

    if ($ClearIncidentHistory) {
        if (Test-Path -LiteralPath $DatabasePath) {
            Remove-Item -LiteralPath $DatabasePath
            Write-Host "Cleared retained local incident history: $DatabasePath"
        }
        else {
            Write-Host "No retained local incident history exists."
        }
    }

    Write-Host "Checkout is back at the controlled healthy baseline."
    if (-not $ClearIncidentHistory) {
        Write-Host "Retained local incident history was preserved. Use -ClearIncidentHistory to reset it with the local API restarted automatically."
    }
    elseif ($restartApi) {
        Start-LocalOpsPilotApi -ControlledSimulation $restartControlledSimulation
    }
}
finally {
    Pop-Location
}

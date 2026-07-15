$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

Push-Location $Root
try {
    uv run ruff check backend tests
    Assert-LastExitCode "Ruff"
    uv run ty check backend
    Assert-LastExitCode "Ty"
    uv run pytest
    Assert-LastExitCode "Pytest"
    Push-Location frontend
    try {
        npm ci
        Assert-LastExitCode "npm ci"
        npm run build
        Assert-LastExitCode "Frontend build"
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}

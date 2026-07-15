param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not set. Create ignored .env from .env.example; do not put a key in source control."
}

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = "artifacts/model-selection-$timestamp.json"
}

$parent = Split-Path -Parent $OutputPath
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $Root "backend"
    Push-Location $Root
    $result = uv run python -c "import json; from opspilot.model_selection import run_model_selection_fixture; from opspilot.settings import Settings; print(json.dumps(run_model_selection_fixture(Settings()), sort_keys=True))"
    $result | Set-Content -Encoding utf8 $OutputPath
    Get-Content -Raw $OutputPath
}
finally {
    Pop-Location
    $env:PYTHONPATH = $previousPythonPath
}

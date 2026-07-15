[CmdletBinding()]
param(
    [string]$Endpoint = "http://127.0.0.1:8000/api/v1/ingress/alertmanager",
    [string]$ScenarioRunId = ("p1-" + (Get-Date -Format "yyyyMMddHHmmss")),
    [string]$Secret = ""
)

$headers = @{}
if ($Secret) {
    $headers["X-OpsPilot-Scenario-Secret"] = $Secret
}

$now = (Get-Date).ToUniversalTime().ToString("o")
$labels = @{
    alertname = "Checkout5xxHigh"
    service = "checkout"
    severity = "critical"
    opspilot_run_id = $ScenarioRunId
}
$payload = @{
    version = "4"
    groupKey = "{alertname=`"Checkout5xxHigh`",opspilot_run_id=`"$ScenarioRunId`"}"
    truncatedAlerts = 0
    status = "firing"
    receiver = "opspilot"
    groupLabels = @{ alertname = "Checkout5xxHigh"; opspilot_run_id = $ScenarioRunId }
    commonLabels = $labels
    commonAnnotations = @{ summary = "Checkout 5xx rate is above the controlled threshold" }
    externalURL = "http://alertmanager.local"
    alerts = @(@{
        status = "firing"
        labels = $labels
        annotations = @{ summary = "Checkout 5xx rate is above the controlled threshold" }
        startsAt = $now
        endsAt = "0001-01-01T00:00:00Z"
        generatorURL = "http://prometheus.local/graph?g0.expr=checkout"
        fingerprint = "checkout-5xx-$ScenarioRunId"
    })
}

Invoke-RestMethod -Method Post -Uri $Endpoint -Headers $headers -ContentType "application/json" -Body ($payload | ConvertTo-Json -Depth 8)

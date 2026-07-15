import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

app = FastAPI(title="OpsPilot checkout demo")
requests = Counter(
    "http_requests_total",
    "HTTP requests served by the controlled checkout service",
    ["service", "status"],
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/checkout")
def checkout() -> JSONResponse:
    if os.environ.get("FAIL_MODE", "false").lower() == "true":
        requests.labels(service="checkout", status="500").inc()
        return JSONResponse(status_code=500, content={"error": "controlled checkout failure"})

    requests.labels(service="checkout", status="200").inc()
    return JSONResponse(status_code=200, content={"status": "accepted"})


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opspilot.api.main import create_app
from opspilot.settings import Settings


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        OPS_PILOT_DB_PATH=tmp_path / "opspilot.db",
        OPS_PILOT_ALERT_SHARED_SECRET="test-secret",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client

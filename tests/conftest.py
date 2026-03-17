import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())

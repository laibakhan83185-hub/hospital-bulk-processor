import pytest

from app.services import hospital_service


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """Skip backoff sleeps so retry tests run instantly."""
    monkeypatch.setattr(hospital_service, "RETRY_BASE_DELAY", 0)


@pytest.fixture(autouse=True)
def skip_warm_up(request, monkeypatch):
    """Skip the cold-start warm-up call unless a test opts in."""
    if "warm_up" in request.keywords:
        return

    async def no_warm_up(client, batch_id):
        return None

    monkeypatch.setattr(hospital_service, "warm_up", no_warm_up)

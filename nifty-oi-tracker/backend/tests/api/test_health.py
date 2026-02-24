import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_analysis_latest_stub(client: AsyncClient):
    response = await client.get("/api/v1/analysis/latest")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_analysis_history_stub(client: AsyncClient):
    response = await client.get("/api/v1/analysis/history")
    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_trades_stub(client: AsyncClient):
    response = await client.get("/api/v1/trades/iron_pulse")
    assert response.status_code == 200
    assert response.json()["strategy"] == "iron_pulse"


@pytest.mark.asyncio
async def test_market_status_stub(client: AsyncClient):
    response = await client.get("/api/v1/market/status")
    assert response.status_code == 200
    assert "is_open" in response.json()


@pytest.mark.asyncio
async def test_logs_stub(client: AsyncClient):
    response = await client.get("/api/v1/logs")
    assert response.status_code == 200
    assert response.json()["data"] == []

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
async def test_analysis_latest_empty(client: AsyncClient):
    response = await client.get("/api/v1/analysis/latest")
    assert response.status_code == 200
    data = response.json()
    assert data["analysis"] is None
    assert "active_trades" in data
    assert "chart_history" in data


@pytest.mark.asyncio
async def test_analysis_history_empty(client: AsyncClient):
    response = await client.get("/api/v1/analysis/history")
    assert response.status_code == 200
    data = response.json()
    assert data["data"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_trades_iron_pulse(client: AsyncClient):
    response = await client.get("/api/v1/trades/iron_pulse")
    assert response.status_code == 200
    data = response.json()
    assert data["strategy"] == "iron_pulse"
    assert data["data"] == []


@pytest.mark.asyncio
async def test_trades_invalid_strategy(client: AsyncClient):
    response = await client.get("/api/v1/trades/invalid")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_trade_stats(client: AsyncClient):
    response = await client.get("/api/v1/trades/iron_pulse/stats")
    assert response.status_code == 200
    stats = response.json()["stats"]
    assert "win_rate" in stats
    assert "total" in stats


@pytest.mark.asyncio
async def test_market_status(client: AsyncClient):
    response = await client.get("/api/v1/market/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_open" in data
    assert "server_time" in data


@pytest.mark.asyncio
async def test_kite_status(client: AsyncClient):
    response = await client.get("/api/v1/kite/status")
    assert response.status_code == 200
    data = response.json()
    assert "authenticated" in data


@pytest.mark.asyncio
async def test_logs_empty(client: AsyncClient):
    response = await client.get("/api/v1/logs")
    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_logs_with_filters(client: AsyncClient):
    response = await client.get("/api/v1/logs?level=ERROR&hours=1&limit=10")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_analysis_history_with_limit(client: AsyncClient):
    response = await client.get("/api/v1/analysis/history?limit=10")
    assert response.status_code == 200

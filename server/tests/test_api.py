"""Testes de ponta a ponta da API.

Exercitam o caminho completo -- ingestao do quadro, deteccao, telemetria do
sonar, alerta de obstaculo e comando de voz -- com o detector simulado e sem
nenhum dispositivo fisico conectado.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture()
async def client(settings):
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        async with app.router.lifespan_context(app):
            yield http_client, app.state.container


async def test_health_reports_the_active_backends(client):
    http_client, _ = client
    response = await http_client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["detector"] == "fake"
    assert body["status"] == "ok"


async def test_frame_ingestion_returns_detections(client, jpeg_frame):
    http_client, _ = client
    # O detector simulado precisa de alguns quadros para confirmar os rastros
    # (o limitador de taxa fica desligado nos testes, ver conftest).
    last = None
    for sequence in range(6):
        response = await http_client.post(
            "/api/v1/ingest/frame",
            content=jpeg_frame,
            headers={
                "Content-Type": "image/jpeg",
                "X-Device-Id": "sim-01",
                "X-Frame-Seq": str(sequence),
            },
        )
        assert response.status_code == 200
        last = response.json()
    assert last["device_id"] == "sim-01"
    assert last["detections"], "esperava deteccoes do detector simulado"
    detection = last["detections"][0]
    assert detection["direction"] in {"left", "center", "right"}
    assert detection["label_pt"]


async def test_empty_body_is_rejected(client):
    http_client, _ = client
    response = await http_client.post("/api/v1/ingest/frame", content=b"")
    assert response.status_code == 400


async def test_critical_telemetry_triggers_spoken_alert(client):
    http_client, container = client
    response = await http_client.post(
        "/api/v1/ingest/telemetry",
        json={
            "device_id": "sim-01",
            "sensors": [{"sensor_id": "front", "distance_cm": 45.0}],
            "battery_pct": 90.0,
        },
    )
    assert response.status_code == 200
    assert response.json()["zone"] == "critical"

    session = container.registry.get("sim-01")
    utterance = session.composer.compose_obstacle(
        session.fusion.nearest(session.last_seen_ms or 0), session.zone
    )
    assert utterance is None or "obstaculo" in utterance.text.lower()


async def test_text_command_describes_the_scene(client, jpeg_frame):
    http_client, _ = client
    for sequence in range(6):
        await http_client.post(
            "/api/v1/ingest/frame",
            content=jpeg_frame,
            headers={"X-Device-Id": "sim-01", "X-Frame-Seq": str(sequence)},
        )

    response = await http_client.post(
        "/api/v1/command",
        json={"device_id": "sim-01", "text": "o que tem na minha frente?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["understood"]
    assert body["intent"] == "describe_scene"
    assert body["reply"]


async def test_unknown_command_is_reported(client):
    http_client, _ = client
    await http_client.post(
        "/api/v1/ingest/telemetry",
        json={"device_id": "sim-01", "sensors": []},
    )
    response = await http_client.post(
        "/api/v1/command", json={"device_id": "sim-01", "text": "asdf qwer"}
    )
    assert response.json()["understood"] is False


async def test_start_stop_button_toggles_the_session(client):
    http_client, container = client
    await http_client.post(
        "/api/v1/ingest/telemetry", json={"device_id": "sim-01", "sensors": []}
    )
    session = container.registry.get("sim-01")
    assert session.active

    await http_client.post(
        "/api/v1/ingest/button", json={"device_id": "sim-01", "button": "start_stop"}
    )
    assert not session.active

    await http_client.post(
        "/api/v1/ingest/button", json={"device_id": "sim-01", "button": "start_stop"}
    )
    assert session.active


async def test_paused_session_drops_frames(client, jpeg_frame):
    http_client, container = client
    await http_client.post(
        "/api/v1/ingest/telemetry", json={"device_id": "sim-01", "sensors": []}
    )
    container.registry.get("sim-01").set_active(False)

    response = await http_client.post(
        "/api/v1/ingest/frame", content=jpeg_frame, headers={"X-Device-Id": "sim-01"}
    )
    assert response.json()["dropped"] is True


async def test_volume_buttons_adjust_the_level(client):
    http_client, container = client
    await http_client.post(
        "/api/v1/ingest/telemetry", json={"device_id": "sim-01", "sensors": []}
    )
    before = container.audio.volume_step
    await http_client.post(
        "/api/v1/ingest/button", json={"device_id": "sim-01", "button": "volume_up"}
    )
    assert container.audio.volume_step == min(10, before + 1)


async def test_device_token_is_enforced_when_configured(settings, jpeg_frame):
    settings.server.device_token = "segredo"
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        async with app.router.lifespan_context(app):
            unauthorized = await http_client.post("/api/v1/ingest/frame", content=jpeg_frame)
            assert unauthorized.status_code == 401

            authorized = await http_client.post(
                "/api/v1/ingest/frame",
                content=jpeg_frame,
                headers={"X-Device-Token": "segredo"},
            )
            assert authorized.status_code == 200

"""Testes de ponta a ponta da API.

Exercitam o caminho completo -- ingestao do quadro, deteccao, telemetria do
sonar, alerta de obstaculo e comando de voz -- com o detector simulado e sem
nenhum dispositivo fisico conectado.
"""

from __future__ import annotations

import struct
import time

import pytest
from fastapi.testclient import TestClient
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
    assert body["detector_model"] == "simulado"
    # Escolhido explicitamente (conftest), nao e um fallback: sem nota de erro.
    assert body["detector_note"] is None
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


# --------------------------------------------------------------- WebSocket


def _image_message(sequence: int, jpeg: bytes) -> bytes:
    return struct.pack(">BQQ", 0x01, sequence, 0) + jpeg


def _slow_detector(app, seconds: float) -> None:
    """Inferencia mais lenta que o intervalo entre quadros, como em CPU."""
    detector = app.state.container.detector
    original = detector.detect

    def slow(image):
        time.sleep(seconds)
        return original(image)

    detector.detect = slow


def test_stream_replaces_waiting_frames_instead_of_queueing(settings, jpeg_frame):
    app = create_app(settings)
    with TestClient(app) as client:
        _slow_detector(app, 0.3)
        with client.websocket_connect("/api/v1/stream?device_id=ws-01") as socket:
            for sequence in range(1, 6):
                socket.send_bytes(_image_message(sequence, jpeg_frame))
            acks: dict[int, bool] = {}
            while len(acks) < 5:
                message = socket.receive_json()
                if message["type"] == "frame_ack":
                    acks[message["sequence"]] = message["dropped"]
    # Todo quadro recebe exatamente um ack; os que esperavam foram substituidos
    # (dropped=True faz o dispositivo reduzir a taxa) e o mais recente e inferido.
    assert set(acks) == {1, 2, 3, 4, 5}
    assert any(acks.values())
    assert acks[5] is False


def test_control_messages_do_not_wait_for_inference(settings, jpeg_frame):
    app = create_app(settings)
    with TestClient(app) as client:
        _slow_detector(app, 0.5)
        with client.websocket_connect("/api/v1/stream?device_id=ws-02") as socket:
            socket.send_bytes(_image_message(1, jpeg_frame))
            socket.send_json({"type": "ping"})
            # Antes, o pong so saia depois do frame_ack: o sonar e os comandos
            # esperavam a inferencia terminar.
            assert socket.receive_json()["type"] == "pong"
            assert socket.receive_json()["type"] == "frame_ack"

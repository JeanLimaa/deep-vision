"""DTOs da API HTTP/WebSocket (contrato com firmware, simulador e dashboard)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.types import Direction, Priority, Zone


class SensorReadingIn(BaseModel):
    sensor_id: str = "front"
    # Nulo quando o sensor nao recebeu eco dentro do timeout.
    distance_cm: float | None = None


class TelemetryIn(BaseModel):
    device_id: str
    sensors: list[SensorReadingIn] = Field(default_factory=list)
    battery_pct: float | None = None
    rssi_dbm: int | None = None
    uptime_ms: int | None = None
    free_heap: int | None = None
    firmware: str | None = None


class ButtonEventIn(BaseModel):
    device_id: str
    button: Literal["power", "start_stop", "volume_up", "volume_down"]
    action: Literal["press", "long_press", "release"] = "press"


class TextCommandIn(BaseModel):
    device_id: str | None = None
    text: str
    # Origem util para o log: dashboard, simulador ou STT do dispositivo.
    source: Literal["dashboard", "simulator", "device", "stt"] = "dashboard"


class DetectionOut(BaseModel):
    label: str
    label_pt: str
    confidence: float
    box: list[float]
    direction: Direction
    distance_m: float | None = None
    track_id: int | None = None


class ActionOut(BaseModel):
    """Acao que o servidor devolve para a borda executar."""

    type: Literal["speak", "tone", "config", "volume", "state"]
    text: str | None = None
    audio_url: str | None = None
    tone: str | None = None
    priority: int = Priority.INFO.value
    interrupt: bool = False
    data: dict[str, Any] = Field(default_factory=dict)


class FrameResultOut(BaseModel):
    device_id: str
    sequence: int
    detections: list[DetectionOut] = Field(default_factory=list)
    inference_ms: float = 0.0
    total_ms: float = 0.0
    dropped: bool = False
    actions: list[ActionOut] = Field(default_factory=list)


class SessionStateOut(BaseModel):
    device_id: str
    online: bool
    active: bool
    volume_step: int
    zone: Zone
    nearest_distance_m: float | None = None
    frames_received: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    avg_inference_ms: float = 0.0
    # Captura -> resposta. Cai para o tempo interno do servidor quando os
    # relogios do dispositivo e do servidor nao estao sincronizados.
    avg_end_to_end_ms: float = 0.0
    fps: float = 0.0
    last_seen_ms: int | None = None
    battery_pct: float | None = None
    rssi_dbm: int | None = None


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    detector: str
    detector_ready: bool
    tts: str
    stt: str
    audio_sinks: list[str]
    devices: list[SessionStateOut] = Field(default_factory=list)


class CommandResultOut(BaseModel):
    understood: bool
    intent: str | None = None
    reply: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)

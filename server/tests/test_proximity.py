"""Zonas de proximidade -- o requisito de seguranca da Secao 5.4 do TCC."""

from __future__ import annotations

from app.core.types import Direction, Zone
from app.proximity.fusion import ProximityFusion
from app.proximity.zones import MedianFilter, ZoneClassifier
from app.settings import ProximitySettings


def make_classifier(**overrides) -> ZoneClassifier:
    return ZoneClassifier(ProximitySettings(**overrides))


def test_zone_thresholds_match_the_paper():
    zones = make_classifier()
    assert zones.classify("front", 2.00) is Zone.SAFE
    assert zones.classify("front", 1.20) is Zone.WARNING
    assert zones.classify("front", 0.50) is Zone.CRITICAL


def test_hysteresis_prevents_flapping_at_the_boundary():
    zones = make_classifier(critical_distance_m=0.80, hysteresis_m=0.10)
    assert zones.classify("front", 0.78) is Zone.CRITICAL
    # Ainda critico: nao basta cruzar o limite, precisa da folga de histerese.
    assert zones.classify("front", 0.85) is Zone.CRITICAL
    assert zones.classify("front", 0.95) is Zone.WARNING


def test_missing_echo_is_treated_as_free_path():
    zones = make_classifier()
    zones.classify("front", 0.40)
    assert zones.classify("front", None) is Zone.SAFE


def test_out_of_range_reading_is_discarded():
    zones = make_classifier(max_valid_distance_m=4.0)
    assert zones.classify("front", 9.9) is Zone.SAFE


def test_median_filter_removes_isolated_spike():
    filtro = MedianFilter(window=5)
    for value in (1.0, 1.0, 1.0):
        filtro.push("front", value)
    # Um outlier isolado nao deve alterar a saida.
    assert filtro.push("front", 0.05) == 1.0


def test_fusion_reports_worst_zone_across_sensors():
    settings = ProximitySettings()
    fusion = ProximityFusion(settings)
    zones = ZoneClassifier(settings)
    fusion.update(zones.reading("left", 300))
    fusion.update(zones.reading("front", 60))
    from app.core.types import now_ms

    assert fusion.zone(now_ms()) is Zone.CRITICAL
    assert fusion.nearest(now_ms()).sensor_id == "front"


def test_stale_readings_expire():
    settings = ProximitySettings(reading_ttl_ms=100)
    fusion = ProximityFusion(settings)
    zones = ZoneClassifier(settings)
    reading = zones.reading("front", 40)
    fusion.update(reading)
    assert fusion.zone(reading.measured_at_ms) is Zone.CRITICAL
    # Passado o TTL, a leitura nao pode mais manter o alerta ativo.
    assert fusion.zone(reading.measured_at_ms + 500) is Zone.SAFE


def test_fusion_replaces_visual_estimate_when_consistent():
    from app.core.types import BoundingBox, TrackedObject

    settings = ProximitySettings()
    fusion = ProximityFusion(settings)
    zones = ZoneClassifier(settings)
    reading = zones.reading("front", 90)
    fusion.update(reading)

    track = TrackedObject(
        track_id=1,
        label="chair",
        confidence=0.8,
        box=BoundingBox(0, 0, 10, 10),
        direction=Direction.CENTER,
        distance_m=1.10,
        first_seen_ms=0,
        last_seen_ms=0,
    )
    fusion.refine_tracks([track], reading.measured_at_ms)
    assert track.distance_m == 0.9

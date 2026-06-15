from lib.movement_events import parse_iso
from lib.movement_start import estimate_movement_start


def _candle(ts, close):
    return {
        "timestamp": parse_iso(ts),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
    }


def test_estimate_pump_start_from_local_low_threshold():
    candles = [
        _candle("2026-06-10T00:00:00Z", 1.00),
        _candle("2026-06-10T00:05:00Z", 0.90),
        _candle("2026-06-10T00:10:00Z", 0.95),
        _candle("2026-06-10T00:15:00Z", 1.04),
        _candle("2026-06-10T00:20:00Z", 1.20),
    ]

    estimate = estimate_movement_start(
        candles,
        direction="pump",
        window_start=parse_iso("2026-06-10T00:00:00Z"),
        detected_at=parse_iso("2026-06-10T00:20:00Z"),
        threshold_pct=15,
    )

    assert estimate["start_at"] == "2026-06-10T00:15:00Z"
    assert estimate["peak_at"] == "2026-06-10T00:20:00Z"


def test_estimate_dump_start_from_local_high_threshold():
    candles = [
        _candle("2026-06-10T00:00:00Z", 1.00),
        _candle("2026-06-10T00:05:00Z", 1.20),
        _candle("2026-06-10T00:10:00Z", 1.10),
        _candle("2026-06-10T00:15:00Z", 1.00),
        _candle("2026-06-10T00:20:00Z", 0.80),
    ]

    estimate = estimate_movement_start(
        candles,
        direction="dump",
        window_start=parse_iso("2026-06-10T00:00:00Z"),
        detected_at=parse_iso("2026-06-10T00:20:00Z"),
        threshold_pct=15,
    )

    assert estimate["start_at"] == "2026-06-10T00:15:00Z"
    assert estimate["trough_at"] == "2026-06-10T00:20:00Z"

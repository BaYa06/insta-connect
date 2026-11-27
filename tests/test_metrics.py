from flashly.metrics import build_media_metric, extract_value, ms_to_seconds


def test_ms_to_seconds():
    assert ms_to_seconds(5400) == 5.4
    assert ms_to_seconds(0) == 0.0
    assert ms_to_seconds(1) == 0.001
    assert ms_to_seconds(99360000) == 99360.0


def test_avg_watch_time_converted_with_units_and_raw_kept():
    m = build_media_metric("ig_reels_avg_watch_time", {"values": [{"value": 5400}]})
    assert (m.value, m.unit, m.raw_value, m.raw_unit, m.aggregation) == (5.4, "seconds", 5400, "milliseconds", "average")
    assert "retention" in m.note.lower() and "no per-second" in m.note.lower()


def test_total_watch_time_converted():
    m = build_media_metric("ig_reels_video_view_total_time", {"values": [{"value": 120000}]})
    assert m.value == 120.0 and m.aggregation == "total" and m.raw_value == 120000


def test_zero_is_real_value_not_null():
    m = build_media_metric("ig_reels_avg_watch_time", {"values": [{"value": 0}]})
    assert m.available and m.value == 0.0
    c = build_media_metric("saved", {"values": [{"value": 0}]})
    assert c.available and c.value == 0


def test_missing_is_null_with_reason_never_a_placeholder():
    for item in (None, {"values": []}, {"values": [{"value": None}]}, {}):
        m = build_media_metric("shares", item)
        assert not m.available and m.value is None and m.raw_value is None and m.reason


def test_count_metric_has_no_time_conversion():
    m = build_media_metric("views", {"values": [{"value": 1234}]})
    assert (m.value, m.unit, m.raw_unit) == (1234, "count", "count")


def test_extract_value_total_value_shape():
    assert extract_value({"total_value": {"value": 7}}) == 7

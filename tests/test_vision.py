"""
tests/test_vision.py - Tests for advisory photo comparison module (P3 stretch).
"""
from app.vision.compare import compare_photos, compute_photo_hash

def test_identical_photos():
    data = b"image_raw_bytes_test_12345678" * 50
    result = compare_photos(data, data, angle="front")

    assert result["score"] == 0.0
    assert result["requires_human_review"] is False
    assert len(result["changed_regions"]) == 0
    assert result["before_sha256"] == result["after_sha256"]
    assert "identical" in result["note"].lower()

def test_different_photos_triggers_human_review():
    data_before = b"\x00" * 500
    data_after = b"\xFF" * 500

    result = compare_photos(data_before, data_after, angle="top")

    assert result["score"] > 0.5
    assert result["requires_human_review"] is True
    assert len(result["changed_regions"]) > 0
    assert "human review needed" in result["note"].lower()
    assert result["before_sha256"] != result["after_sha256"]


# tests/test_sync.py
import io
import json
from unittest.mock import patch, MagicMock
from datetime import date

import requests
import sys
sys.path.insert(0, ".")

from PIL import Image
from sync import search_images_for_date, download_thumbnail, resize_and_letterbox, apply_eink_effects
import numpy as np
import os


def test_apply_eink_effects_saves_spectra6(tmp_path):
    img = Image.new("RGB", (1200, 1600), color=(100, 100, 100))
    output_path = tmp_path / "test.spectra6"
    apply_eink_effects(img, output_path)
    assert output_path.exists()
    with open(output_path, "rb") as f:
        header = f.read(8)
        assert header == b"SPECTRA6"


def test_run_sync_applies_dithering_when_env_set(tmp_path):
    img = Image.new("RGB", (300, 400), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    fake_jpeg = buf.getvalue()

    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = {
        "assets": {
            "items": [
                {"id": "asset-dither", "exifInfo": {"exifImageWidth": 300, "exifImageHeight": 400}},
            ],
            "total": 1,
        }
    }

    mock_thumb_resp = MagicMock()
    mock_thumb_resp.status_code = 200
    mock_thumb_resp.content = fake_jpeg

    with patch("sync.requests.post", return_value=mock_search_resp), \
         patch("sync.requests.get", return_value=mock_thumb_resp), \
         patch("sync.IMAGE_DIR", tmp_path), \
         patch.dict(os.environ, {"DITHER": "true"}), \
         patch("sync.apply_eink_effects", wraps=apply_eink_effects) as mock_apply:
        
        from sync import run_sync
        run_sync(
            api_url="http://immich:2283/api",
            api_key="test-key",
            years_back=1,
        )

    assert mock_apply.called
    # Both JPEG and spectra6 should exist
    saved_jpgs = list(tmp_path.glob("*.jpg"))
    assert len(saved_jpgs) == 1
    
    saved_s6 = list(tmp_path.glob("*.spectra6"))
    assert len(saved_s6) == 1
    assert "asset-dither" in saved_s6[0].name


def test_search_images_for_date_returns_asset_ids():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "assets": {
            "items": [
                {"id": "abc-123", "exifInfo": {"exifImageWidth": 3000, "exifImageHeight": 4000}},
                {"id": "def-456", "exifInfo": {"exifImageWidth": 4000, "exifImageHeight": 3000}},
            ],
            "total": 2,
        }
    }

    with patch("sync.requests.post", return_value=mock_response) as mock_post:
        ids = search_images_for_date(
            api_url="http://immich:2283/api",
            api_key="test-key",
            target_date=date(2023, 3, 29),
        )

    # Only the portrait image (3000x4000) should be returned
    assert ids == ["abc-123"]
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    body = call_kwargs[1]["json"]
    assert body["type"] == "IMAGE"
    assert body["size"] == 1000
    assert "takenAfter" in body
    assert "takenBefore" in body


def test_download_thumbnail_returns_image_bytes():
    fake_image_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = fake_image_data

    with patch("sync.requests.get", return_value=mock_response) as mock_get:
        data = download_thumbnail(
            api_url="http://immich:2283/api",
            api_key="test-key",
            asset_id="abc-123",
        )

    assert data == fake_image_data
    mock_get.assert_called_once_with(
        "http://immich:2283/api/assets/abc-123/thumbnail?size=preview",
        headers={"x-api-key": "test-key"},
    )


def test_resize_portrait_image_to_target():
    img = Image.new("RGB", (3000, 4000), color="blue")
    result = resize_and_letterbox(img)
    assert result.size == (1200, 1600)


def test_resize_exact_size_unchanged():
    img = Image.new("RGB", (1200, 1600), color="green")
    result = resize_and_letterbox(img)
    assert result.size == (1200, 1600)


def test_resize_narrow_portrait_letterboxed():
    img = Image.new("RGB", (600, 1600), color="red")
    result = resize_and_letterbox(img)
    assert result.size == (1200, 1600)
    assert result.getpixel((0, 0)) == (255, 255, 255)


def test_resize_close_ratio_crops_to_fill():
    """An image within 15% of the target ratio should crop-to-fill, no letterbox."""
    # 3:4 target is 0.75. 1300x1600 is 0.8125 — ~8% wider, within threshold.
    img = Image.new("RGB", (1300, 1600), color="blue")
    result = resize_and_letterbox(img)
    assert result.size == (1200, 1600)
    # No white letterbox — all blue (cropped, not padded)
    assert result.getpixel((0, 0)) == (0, 0, 255)
    assert result.getpixel((1199, 1599)) == (0, 0, 255)


from pathlib import Path
from sync import run_sync


def test_run_sync_downloads_and_saves_images(tmp_path):
    """run_sync should search, download, resize, save images, and clean up stale files."""
    # Create a stale file that should be cleaned up
    stale_file = tmp_path / "2020_old-asset.jpg"
    stale_file.write_bytes(b"stale")

    # Create a small valid portrait JPEG for the mock download
    img = Image.new("RGB", (300, 400), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    fake_jpeg = buf.getvalue()

    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = {
        "assets": {
            "items": [
                {"id": "asset-1", "exifInfo": {"exifImageWidth": 3000, "exifImageHeight": 4000}},
            ],
            "total": 1,
        }
    }

    mock_thumb_resp = MagicMock()
    mock_thumb_resp.status_code = 200
    mock_thumb_resp.content = fake_jpeg

    with patch("sync.requests.post", return_value=mock_search_resp), \
         patch("sync.requests.get", return_value=mock_thumb_resp), \
         patch("sync.IMAGE_DIR", tmp_path):
        run_sync(
            api_url="http://immich:2283/api",
            api_key="test-key",
            years_back=1,
        )

    # Stale file should be removed
    assert not stale_file.exists()

    # New images should exist (both jpg and spectra6)
    saved_jpgs = list(tmp_path.glob("*.jpg"))
    assert len(saved_jpgs) == 1
    assert "asset-1" in saved_jpgs[0].name

    saved_s6 = list(tmp_path.glob("*.spectra6"))
    assert len(saved_s6) == 1
    assert "asset-1" in saved_s6[0].name

    # Verify it's a valid spectra6 file
    with open(saved_s6[0], "rb") as f:
        assert f.read(8) == b"SPECTRA6"


def test_run_sync_skips_cleanup_on_total_failure(tmp_path):
    """If all searches fail, existing images should NOT be deleted."""
    existing_file = tmp_path / "2023_good-asset.jpg"
    existing_file.write_bytes(b"keep me")

    with patch("sync.requests.post", side_effect=requests.RequestException("connection refused")), \
         patch("sync.IMAGE_DIR", tmp_path):
        run_sync(
            api_url="http://immich:2283/api",
            api_key="test-key",
            years_back=1,
        )

    # File should still exist — cleanup was skipped
    assert existing_file.exists()

# sync.py
"""Sync 'on this day' images from Immich and serve via HTTP (random image per request)."""

import io
import logging
import os
import random
import sys
import threading
import time
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import unittest.mock

import requests
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import numpy as np

# Add the source directory to sys.path to import dithering logic
SOURCE_REPO_PATH = os.environ.get("SOURCE_REPO_PATH", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "PhotoPainter-E-Ink-Spectra-6-image-converter")))
sys.path.append(SOURCE_REPO_PATH)

# Mock sys.argv and sys.exit to avoid argparse errors and script termination on import
with unittest.mock.patch('sys.argv', ['sync.py', '--dir', 'portrait', 'dummy']), \
     unittest.mock.patch('sys.exit'), \
     unittest.mock.patch('builtins.print'), \
     unittest.mock.patch('ConvertTo6ColorsForEInkSpectra6.tqdm'):
    try:
        from ConvertTo6ColorsForEInkSpectra6 import PALETTE_COLORS
    except ImportError as e:
        logging.error("Could not import PALETTE_COLORS from source repo at %s: %s", SOURCE_REPO_PATH, e)
        sys.exit(1)

# Bitplane order for .spectra6 output: Black, Yellow, Red, Blue, Green (White has no plane)
_PLANE_COLORS = [PALETTE_COLORS[i] for i in (0, 2, 3, 4, 5)]


def _save_spectra6_fast(quantized_p_img: Image.Image, output_path: Path) -> None:
    """Vectorized .spectra6 writer. Operates on a P-mode image (palette indices)."""
    width, height = quantized_p_img.size
    if (width, height) != (TARGET_WIDTH, TARGET_HEIGHT):
        quantized_p_img = quantized_p_img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.NEAREST)
        width, height = TARGET_WIDTH, TARGET_HEIGHT

    indices = np.frombuffer(quantized_p_img.tobytes(), dtype=np.uint8)
    palette_rgb = np.array(quantized_p_img.getpalette(), dtype=np.uint8).reshape(-1, 3)

    with open(output_path, 'wb') as f:
        f.write(b'SPECTRA6')
        f.write(width.to_bytes(4, 'little'))
        f.write(height.to_bytes(4, 'little'))
        for target in _PLANE_COLORS:
            matching = np.where(np.all(palette_rgb == np.array(target, dtype=np.uint8), axis=1))[0].astype(np.uint8)
            mask = np.isin(indices, matching)
            f.write(np.packbits(mask).tobytes())


def apply_eink_effects(img: Image.Image, output_path: Path) -> None:
    """Apply 180-degree rotation and Floyd-Steinberg dithering for E-Ink Spectra 6."""
    # Rotate 180 degrees before dithering as requested
    img = img.rotate(180)

    # Create the 6-color palette object from the source repo colors
    pal_image = Image.new("P", (1, 1))
    # Flatten PALETTE_COLORS and pad to 256 colors (768 values)
    palette = []
    for r, g, b in PALETTE_COLORS:
        palette.extend((r, g, b))
    palette.extend([0, 0, 0] * (256 - len(PALETTE_COLORS)))
    pal_image.putpalette(palette)

    # Apply enhancements matching ConvertTo6ColorsForEInkSpectra6.py defaults
    img = ImageEnhance.Brightness(img).enhance(1.1)
    img = ImageEnhance.Contrast(img).enhance(1.2)
    img = ImageEnhance.Color(img).enhance(1.2)
    img = img.filter(ImageFilter.EDGE_ENHANCE)
    img = img.filter(ImageFilter.SMOOTH)
    img = img.filter(ImageFilter.SHARPEN)

    # Floyd-Steinberg dithering (Pillow's C implementation). Keep as P-mode for fast bitplane extraction.
    quantized_img = img.quantize(dither=Image.Dither.FLOYDSTEINBERG, palette=pal_image)

    _save_spectra6_fast(quantized_img, output_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

IMAGE_DIR = Path(os.environ.get("IMAGE_DIR", "/data/images"))
TARGET_WIDTH = 1200
TARGET_HEIGHT = 1600


def _is_portrait(item: dict) -> bool:
    """Check if an asset is portrait orientation based on dimensions."""
    exif = item.get("exifInfo") or {}
    width = exif.get("exifImageWidth") or item.get("width") or 0
    height = exif.get("exifImageHeight") or item.get("height") or 0
    if width == 0 or height == 0:
        return False
    return height > width


def search_images_for_date(api_url: str, api_key: str, target_date: date) -> list[str]:
    """Search Immich for portrait IMAGE assets taken on a specific date. Returns list of asset IDs."""
    taken_after = f"{target_date.isoformat()}T00:00:00.000Z"
    taken_before = f"{target_date.isoformat()}T23:59:59.999Z"

    resp = requests.post(
        f"{api_url}/search/metadata",
        headers={"x-api-key": api_key},
        json={
            "takenAfter": taken_after,
            "takenBefore": taken_before,
            "type": "IMAGE",
            "size": 1000,
        },
    )
    resp.raise_for_status()
    items = resp.json()["assets"]["items"]
    return [item["id"] for item in items if _is_portrait(item)]


def download_thumbnail(api_url: str, api_key: str, asset_id: str) -> bytes:
    """Download the preview-size thumbnail for an asset. Returns raw image bytes."""
    resp = requests.get(
        f"{api_url}/assets/{asset_id}/thumbnail?size=preview",
        headers={"x-api-key": api_key},
    )
    resp.raise_for_status()
    return resp.content


def resize_and_letterbox(img: Image.Image, crop_threshold: float = 0.15) -> Image.Image:
    """Resize image to 1200x1600. Crop-to-fill if within threshold, otherwise fit with letterbox."""
    img = ImageOps.exif_transpose(img)

    src_ratio = img.width / img.height
    target_ratio = TARGET_WIDTH / TARGET_HEIGHT

    # How much we'd need to crop (as fraction of one dimension) to fill the frame
    if src_ratio > target_ratio:
        # Image is wider than target — would crop sides
        crop_fraction = 1 - (target_ratio / src_ratio)
    else:
        # Image is taller than target — would crop top/bottom
        crop_fraction = 1 - (src_ratio / target_ratio)

    if crop_fraction <= crop_threshold:
        # Close enough — crop to fill (no letterbox)
        return ImageOps.fit(img, (TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)
    else:
        # Too different — fit with white letterbox
        img.thumbnail((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)
        canvas = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (255, 255, 255))
        x = (TARGET_WIDTH - img.width) // 2
        y = (TARGET_HEIGHT - img.height) // 2
        canvas.paste(img, (x, y))
        return canvas


def run_sync(api_url: str, api_key: str, years_back: int = 5) -> None:
    """Run a full sync: search for today's images across past years, download, resize, save."""
    today = date.today()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    expected_files: set[str] = set()
    any_search_succeeded = False

    for years_ago in range(1, years_back + 1):
        try:
            target_date = today.replace(year=today.year - years_ago)
        except ValueError:
            # Feb 29 in a non-leap year
            continue

        log.info("Searching for images from %s", target_date)
        try:
            asset_ids = search_images_for_date(api_url, api_key, target_date)
            any_search_succeeded = True
        except requests.RequestException:
            log.exception("Failed to search for date %s", target_date)
            continue

        for asset_id in asset_ids:
            is_dithered = os.environ.get("DITHER", "true").lower() == "true"
            filename_jpg = f"{target_date.year}_{asset_id}.jpg"
            expected_files.add(filename_jpg)
            filepath_jpg = IMAGE_DIR / filename_jpg

            filename_s6 = f"{target_date.year}_{asset_id}.spectra6"
            if is_dithered:
                expected_files.add(filename_s6)
            filepath_s6 = IMAGE_DIR / filename_s6

            if filepath_jpg.exists() and (not is_dithered or filepath_s6.exists()):
                log.info("Already have required files for %s, skipping", asset_id)
                continue

            try:
                data = download_thumbnail(api_url, api_key, asset_id)
                img = Image.open(io.BytesIO(data))
                img = resize_and_letterbox(img)
                
                # Always save JPG
                if not filepath_jpg.exists():
                    img.save(filepath_jpg, "JPEG", quality=90)
                    log.info("Saved %s", filename_jpg)
                
                # Optionally save spectra6
                if is_dithered and not filepath_s6.exists():
                    apply_eink_effects(img, filepath_s6)
                    log.info("Saved %s", filename_s6)
            except Exception:
                log.exception("Failed to process asset %s", asset_id)
                continue

    # Only clean up if at least one search succeeded (avoid wiping display on outage)
    if any_search_succeeded:
        for existing in IMAGE_DIR.glob("*.*"):
            if existing.suffix.lower() in (".jpg", ".jpeg", ".spectra6") and existing.name not in expected_files:
                existing.unlink()
                log.info("Removed stale file %s", existing.name)
    else:
        log.warning("All searches failed, skipping cleanup to preserve existing images")


class RandomImageHandler(BaseHTTPRequestHandler):
    """Serves a random image from IMAGE_DIR on every GET request."""

    def do_GET(self):
        # Determine the extension from the requested path or use a default
        if self.path == "/image.jpg":
            requested_ext = ".jpg"
        elif self.path == "/image.spectra6":
            requested_ext = ".spectra6"
        else:
            # For /, etc., redirect to the current default format
            is_dithered = os.environ.get("DITHER", "true").lower() == "true"
            default_ext = "spectra6" if is_dithered else "jpg"
            self.send_response(301)
            self.send_header("Location", f"/image.{default_ext}")
            self.end_headers()
            return
        
        # Look for files matching the requested extension
        images = list(IMAGE_DIR.glob(f"*{requested_ext}"))
        
        if not images:
            if requested_ext == ".jpg":
                # Only return a placeholder if we truly have NO images at all
                all_images = list(IMAGE_DIR.glob("*.jpg")) + list(IMAGE_DIR.glob("*.spectra6"))
                if not all_images:
                    buf = io.BytesIO()
                    Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (255, 255, 255)).save(buf, "JPEG")
                    data = buf.getvalue()
                    content_type = "image/jpeg"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            
            self.send_error(404, "No images found for the requested format")
            return
        else:
            img_path = random.choice(images)
            data = img_path.read_bytes()
            if img_path.suffix.lower() == ".spectra6":
                content_type = "application/octet-stream"
            else:
                content_type = "image/jpeg"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        log.info(format, *args)


def main() -> None:
    api_url = os.environ.get("IMMICH_API_URL")
    api_key = os.environ.get("IMMICH_API_KEY")
    years_back = int(os.environ.get("YEARS_BACK", "5"))
    port = int(os.environ.get("PORT", "8080"))

    if not api_url or not api_key:
        log.error("IMMICH_API_URL and IMMICH_API_KEY are required")
        sys.exit(1)

    # Start HTTP server in background
    server = HTTPServer(("0.0.0.0", port), RandomImageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Serving random images on port %d", port)

    last_sync_date = None
    while True:
        today = date.today()
        if today != last_sync_date:
            log.info("Starting sync for %s", today)
            try:
                run_sync(api_url, api_key, years_back)
                last_sync_date = today
                log.info("Sync complete")
            except Exception:
                log.exception("Sync failed, will retry in 60s")
        time.sleep(60)


if __name__ == "__main__":
    main()

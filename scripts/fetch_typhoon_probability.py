#!/usr/bin/env python3
"""Extract CWA 120-hour typhoon strike probabilities from the official map.

The CWA map is a fixed-layout 400x500 image. Rather than pairing OCR text
across the whole map (which can assign a neighboring city's percentage),
this script reads a small, city-specific crop around each percentage label.
Only a value confirmed by multiple OCR passes is published.
"""
import json
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract
from pytesseract import Output

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "typhoon-probability.json"
DEBUG = ROOT / "artifacts" / "typhoon-probability-debug"
IMAGE_URL = "https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png"

# Coordinates are percentage-label regions in the CWA WSP_CH.png reference
# image (400 x 500). Values are coordinates in the original image:
# (left, top, right, bottom). They deliberately exclude nearby cities' labels.
REFERENCE_SIZE = (400, 500)
PERCENT_ROIS = {
    "é£æ±ç¸£": (116, 95, 151, 116),
    "ééç¸£": (97, 159, 126, 180),
    "æ¾æ¹ç¸£": (84, 247, 116, 270),
    "æ°åå¸": (230, 101, 271, 124),
    "åºéå¸": (303, 91, 344, 114),
    "æ¡åå¸": (214, 119, 254, 142),
    "èºåå¸": (300, 128, 342, 151),
    "æ°ç«¹å¸": (169, 149, 211, 173),
    "æ°ç«¹ç¸£": (238, 164, 278, 187),
    "èæ ç¸£": (158, 179, 197, 202),
    "èºä¸­å¸": (216, 207, 256, 232),
    "å½°åç¸£": (84, 226, 124, 250),
    "åæç¸£": (226, 239, 266, 264),
    "é²æç¸£": (113, 271, 153, 295),
    "åç¾©å¸": (190, 297, 232, 321),
    "åç¾©ç¸£": (104, 313, 145, 337),
    "èºåå¸": (108, 347, 148, 371),
    "é«éå¸": (108, 397, 148, 421),
    "å±æ±ç¸£": (168, 383, 209, 407),
    "å®è­ç¸£": (319, 163, 357, 186),
    "è±è®ç¸£": (298, 238, 340, 261),
    "èºæ±ç¸£": (241, 344, 281, 368),
    "ç¶ å³¶": (294, 350, 333, 374),
    "è­å¶¼": (279, 425, 319, 449),
    "ææ¥": (167, 452, 205, 476),
}
PERCENT_RE = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*[%ï¼]?")

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def download_official_map():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/4.0)",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "image/png,image/*,*/*;q=0.8",
    }
    try:
        response = requests.get(
            IMAGE_URL, params={"_": int(datetime.now().timestamp())},
            timeout=(10, 35), headers=headers, allow_redirects=True
        )
        response.raise_for_status()
    except requests.exceptions.SSLError as err:
        # Temporary fallback for the certificate-chain failure seen in Actions.
        # The final URL is checked against the official CWA domain before use.
        print(f"è­¦åï¼å®æ¹å SSL æè­é©è­å¤±æï¼åè©¦éå®å®æ¹ç¶²ååæ´ï¼{err}", file=sys.stderr)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(
            IMAGE_URL, params={"_": int(datetime.now().timestamp())},
            timeout=(10, 35), headers=headers, allow_redirects=True,
            verify=False
        )
        response.raise_for_status()

    host = (requests.utils.urlparse(response.url).hostname or "").lower()
    if host not in ("www.cwa.gov.tw", "cwa.gov.tw"):
        raise RuntimeError(f"ä¸è¼éæ°å°åè³éæ°£è±¡ç½²ç¶²åï¼åæ­¢ï¼{response.url}")
    if not response.headers.get("content-type", "").lower().startswith("image/"):
        raise RuntimeError(f"æ°£è±¡ç½²åå³ä¸æ¯åçï¼{response.headers.get('content-type')}")
    try:
        image = Image.open(BytesIO(response.content)).convert("RGB")
    except Exception as exc:
        raise RuntimeError(f"å®æ¹åå³å§å®¹ç¡æ³è§£è®çºåçï¼{exc}") from exc
    if image.width < 200 or image.height < 200:
        raise RuntimeError(f"å®æ¹åå°ºå¯¸ç°å¸¸ï¼{image.size}")
    return response, image

def normalize_roi(roi, size):
    """Scale reference coordinates if the official image dimensions change slightly."""
    sx = size[0] / REFERENCE_SIZE[0]
    sy = size[1] / REFERENCE_SIZE[1]
    x1, y1, x2, y2 = roi
    return (
        max(0, round(x1 * sx)),
        max(0, round(y1 * sy)),
        min(size[0], round(x2 * sx)),
        min(size[1], round(y2 * sy)),
    )

def prepare_crop(crop, mode):
    # Percent text is orange on a light background. Several transforms help OCR.
    enlarged = crop.resize((crop.width * 6, crop.height * 6), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(enlarged)
    gray = ImageEnhance.Contrast(gray).enhance(2.4)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    if mode == "gray":
        return gray
    if mode == "threshold":
        return gray.point(lambda px: 255 if px > 175 else 0)
    if mode == "invert-threshold":
        return gray.point(lambda px: 0 if px > 175 else 255)
    return ImageOps.autocontrast(gray).filter(ImageFilter.MedianFilter(size=3))

def read_percent_from_roi(image, city, roi):
    crop_box = normalize_roi(roi, image.size)
    crop = image.crop(crop_box)
    hits = []
    # Save each crop for visual debugging.
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", city)
    crop.save(DEBUG / f"roi-{safe_name}.png")

    # Read digits only. Different page segmentation and image transforms provide
    # independent OCR attempts; publish only a value repeated by >=2 attempts.
    for mode in ("gray", "threshold", "autocontrast"):
        processed = prepare_crop(crop, mode)
        for psm in (7, 8, 13):
            try:
                raw = pytesseract.image_to_string(
                    processed,
                    lang="eng",
                    config=f"--oem 1 --psm {psm} -c tessedit_char_whitelist=0123456789",
                )
            except Exception as exc:
                print(f"OCR è­¦å {city}/{mode}/psm{psm}: {exc}", file=sys.stderr)
                continue
            digits = re.sub(r"\D", "", raw)
            if not digits:
                continue
            # Crop is restricted to one percentage label. Keep plausible 1â100 values.
            if len(digits) > 3:
                continue
            value = int(digits)
            if 0 <= value <= 100:
                hits.append({"percent": value, "mode": mode, "psm": psm, "raw": raw.strip()})

    counts = {}
    for hit in hits:
        counts[hit["percent"]] = counts.get(hit["percent"], 0) + 1
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, {"crop": list(crop_box), "hits": hits, "reason": "no_digits"}
    best_value, best_count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0
    # Require repeatable OCR consensus. If there is a tie, don't guess.
    if best_count < 2 or best_count == second_count:
        return None, {
            "crop": list(crop_box), "hits": hits,
            "counts": counts, "reason": "low_consensus_or_tie"
        }
    return best_value, {
        "crop": list(crop_box), "hits": hits, "counts": counts,
        "accepted_count": best_count
    }

def main():
    DEBUG.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    response, image = download_official_map()
    image.save(DEBUG / "cwa-source.png")

    # Coordinates are designed for the official map layout; a radically different
    # aspect ratio may indicate the CWA has changed its image template.
    aspect = image.width / image.height
    ref_aspect = REFERENCE_SIZE[0] / REFERENCE_SIZE[1]
    if abs(aspect - ref_aspect) > 0.12:
        raise RuntimeError(
            f"å®æ¹æ©çåçåæ¯ä¾å·²æ¹è®ï¼ç®å {image.width}x{image.height}ï¼ï¼"
            "åæ­¢ä½¿ç¨èåº§æ¨ï¼é¿åæç¾åæ¯éé¯ç¸£å¸ã"
        )

    values = {}
    diagnostics = {}
    for city, roi in PERCENT_ROIS.items():
        percent, detail = read_percent_from_roi(image, city, roi)
        diagnostics[city] = detail
        if percent is not None:
            values[city] = {"percent": percent}
            print(f"OCRï¼{city} {percent}%ï¼å¤æ¬¡è¾¨è­ä¸è´ï¼")
        else:
            print(f"OCRï¼{city} æªè½å¯é è¾¨è­ï¼ä¿ç --")

    (DEBUG / "ocr-diagnostics.json").write_text(
        json.dumps({
            "source_url": IMAGE_URL,
            "downloaded_at": utc_now(),
            "image_size": {"width": image.width, "height": image.height},
            "recognized_count": len(values),
            "expected_count": len(PERCENT_ROIS),
            "values": values,
            "details": diagnostics,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if not values:
        raise RuntimeError(
            "æ²æä»»ä½æ©çå¼ééå¤æ¬¡ OCR ä¸è´æ§æª¢æ¥ï¼"
            "å·²è¼¸åºè¨ºæ·åçï¼æªæ´æ°æ­£å¼ JSONã"
        )

    payload = {
        "source": "ä¸­å¤®æ°£è±¡ç½²å®æ¹ 120 å°æé¢±é¢¨æ´é¢¨ä¾µè¥²æ©çå",
        "source_url": IMAGE_URL,
        "generated_at": utc_now(),
        "source_last_modified": response.headers.get("Last-Modified"),
        "image_width": image.width,
        "image_height": image.height,
        "values": values,
        "recognized_count": len(values),
        "expected_count": len(PERCENT_ROIS),
        "ocr_note": (
            "åªç¼å¸å®æ¹æ©çåä¸­ééå¤æ¬¡ OCR ä¸è´æ§æª¢æ¥çç¾åæ¯ï¼"
            "æªè½å¯é è¾¨è­çå°é»ä¸ç¼å¸æ¸å¼ã"
        ),
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(OUT)
    print(f"å·²æ´æ° {OUT}ï¼å¯é è¾¨è­ {len(values)}/{len(PERCENT_ROIS)} åå°é»")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

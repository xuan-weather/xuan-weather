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
    "連江縣": (116, 95, 151, 116),
    "金門縣": (97, 159, 126, 180),
    "澎湖縣": (84, 247, 116, 270),
    "新北市": (230, 101, 271, 124),
    "基隆市": (303, 91, 344, 114),
    "桃園市": (214, 119, 254, 142),
    "臺北市": (300, 128, 342, 151),
    "新竹市": (169, 149, 211, 173),
    "新竹縣": (238, 164, 278, 187),
    "苗栗縣": (158, 179, 197, 202),
    "臺中市": (216, 207, 256, 232),
    "彰化縣": (84, 226, 124, 250),
    "南投縣": (226, 239, 266, 264),
    "雲林縣": (113, 271, 153, 295),
    "嘉義市": (190, 297, 232, 321),
    "嘉義縣": (104, 313, 145, 337),
    "臺南市": (108, 347, 148, 371),
    "高雄市": (108, 397, 148, 421),
    "屏東縣": (168, 383, 209, 407),
    "宜蘭縣": (319, 163, 357, 186),
    "花蓮縣": (298, 238, 340, 261),
    "臺東縣": (241, 344, 281, 368),
    "綠島": (294, 350, 333, 374),
    "蘭嶼": (279, 425, 319, 449),
    "恆春": (167, 452, 205, 476),
}
PERCENT_RE = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*[%％]?")

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
        print(f"警告：官方圖 SSL 憑證驗證失敗，嘗試限定官方網址備援：{err}", file=sys.stderr)
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
        raise RuntimeError(f"下載重新導向至非氣象署網域，停止：{response.url}")
    if not response.headers.get("content-type", "").lower().startswith("image/"):
        raise RuntimeError(f"氣象署回傳不是圖片：{response.headers.get('content-type')}")
    try:
        image = Image.open(BytesIO(response.content)).convert("RGB")
    except Exception as exc:
        raise RuntimeError(f"官方回傳內容無法解讀為圖片：{exc}") from exc
    if image.width < 200 or image.height < 200:
        raise RuntimeError(f"官方圖尺寸異常：{image.size}")
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
                print(f"OCR 警告 {city}/{mode}/psm{psm}: {exc}", file=sys.stderr)
                continue
            digits = re.sub(r"\D", "", raw)
            if not digits:
                continue
            # Crop is restricted to one percentage label. Keep plausible 1–100 values.
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
            f"官方機率圖版型比例已改變（目前 {image.width}x{image.height}），"
            "停止使用舊座標，避免把百分比配錯縣市。"
        )

    values = {}
    diagnostics = {}
    for city, roi in PERCENT_ROIS.items():
        percent, detail = read_percent_from_roi(image, city, roi)
        diagnostics[city] = detail
        if percent is not None:
            values[city] = {"percent": percent}
            print(f"OCR：{city} {percent}%（多次辨識一致）")
        else:
            print(f"OCR：{city} 未能可靠辨識，保留 --")

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
            "沒有任何機率值通過多次 OCR 一致性檢查；"
            "已輸出診斷圖片，未更新正式 JSON。"
        )

    payload = {
        "source": "中央氣象署官方 120 小時颱風暴風侵襲機率圖",
        "source_url": IMAGE_URL,
        "generated_at": utc_now(),
        "source_last_modified": response.headers.get("Last-Modified"),
        "image_width": image.width,
        "image_height": image.height,
        "values": values,
        "recognized_count": len(values),
        "expected_count": len(PERCENT_ROIS),
        "ocr_note": (
            "只發布官方機率圖中通過多次 OCR 一致性檢查的百分比；"
            "未能可靠辨識的地點不發布數值。"
        ),
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(OUT)
    print(f"已更新 {OUT}：可靠辨識 {len(values)}/{len(PERCENT_ROIS)} 個地點")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

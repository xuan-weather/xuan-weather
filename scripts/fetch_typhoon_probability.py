#!/usr/bin/env python3
"""Extract CWA 120-hour typhoon probabilities from the official WSP_CH.png.

Reads percentage labels from city-specific crops measured against the actual
400x500 CWA source image. Ambiguous readings are omitted, never guessed.
"""
import json
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "typhoon-probability.json"
DEBUG = ROOT / "artifacts" / "typhoon-probability-debug"
IMAGE_URL = "https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png"
REFERENCE_SIZE = (400, 500)

# Coordinates enclose the percentage text, not the city name.
# Format: (left, top, right, bottom), based on the actual 400x500 CWA image.
PERCENT_ROIS = {
    "連江縣": (164, 127, 198, 150),
    "金門縣": (143, 188, 166, 211),
    "澎湖縣": (135, 257, 167, 279),
    "新北市": (243, 96, 279, 119),
    "基隆市": (315, 96, 352, 119),
    "桃園市": (223, 124, 261, 149),
    "臺北市": (300, 128, 340, 151),
    "新竹市": (180, 160, 216, 184),
    "新竹縣": (239, 176, 281, 201),
    "苗栗縣": (197, 184, 235, 211),
    "臺中市": (231, 211, 271, 236),
    "彰化縣": (166, 234, 202, 258),
    "南投縣": (243, 246, 281, 271),
    "雲林縣": (160, 268, 198, 292),
    "嘉義市": (211, 296, 250, 321),
    "嘉義縣": (126, 308, 162, 334),
    "臺南市": (126, 351, 163, 380),
    "高雄市": (124, 392, 160, 425),
    "屏東縣": (244, 386, 284, 414),
    "宜蘭縣": (307, 160, 350, 187),
    "花蓮縣": (286, 242, 331, 269),
    "臺東縣": (231, 353, 274, 382),
    "綠島": (296, 350, 340, 379),
    "蘭嶼": (297, 422, 348, 450),
    "恆春": (188, 454, 229, 480),
}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def download_official_map():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/7.0)",
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
        # Temporary compatibility fallback for the observed CWA certificate issue.
        # The final host is validated below; no non-CWA host is accepted.
        print(f"警告：官方圖 SSL 驗證失敗，使用官方網域限定備援：{err}", file=sys.stderr)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(
            IMAGE_URL, params={"_": int(datetime.now().timestamp())},
            timeout=(10, 35), headers=headers, allow_redirects=True, verify=False
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
    sx, sy = size[0] / REFERENCE_SIZE[0], size[1] / REFERENCE_SIZE[1]
    x1, y1, x2, y2 = roi
    return (
        max(0, round(x1 * sx)), max(0, round(y1 * sy)),
        min(size[0], round(x2 * sx)), min(size[1], round(y2 * sy)),
    )

def make_variants(crop):
    enlarged = crop.resize((crop.width * 10, crop.height * 10), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(enlarged)
    gray = ImageEnhance.Contrast(gray).enhance(2.5)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    auto = ImageOps.autocontrast(gray).filter(ImageFilter.MedianFilter(size=3))
    threshold = gray.point(lambda px: 255 if px > 170 else 0)
    return [("gray", gray), ("auto", auto), ("threshold", threshold)]

def parse_percent(raw):
    if not raw:
        return None, False
    cleaned = raw.replace("％", "%").replace(" ", "").replace("\n", "").replace("\r", "")
    # Only accept a one-digit probability when OCR also reads the percent sign.
    match = re.search(r"(?<!\d)(100|[1-9]?\d)\s*(%)?", cleaned)
    if not match:
        return None, False
    value = int(match.group(1))
    if not 0 <= value <= 100:
        return None, False
    return value, match.group(2) == "%"

def read_percent_from_roi(image, city, roi, rapid_engine):
    box = normalize_roi(roi, image.size)
    crop = image.crop(box)
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", city)
    crop.save(DEBUG / f"roi-{safe_name}.png")

    votes, attempts = [], []
    for mode, processed in make_variants(crop):
        for psm in (7, 8, 13):
            try:
                raw = pytesseract.image_to_string(
                    processed, lang="eng",
                    config=f"--oem 1 --psm {psm} -c tessedit_char_whitelist=0123456789%",
                ).strip()
                value, has_pct = parse_percent(raw)
                attempts.append({
                    "engine": "tesseract", "mode": mode, "psm": psm,
                    "raw": raw, "value": value, "has_percent": has_pct,
                })
                if value is not None and (value >= 10 or has_pct):
                    votes.append(("tesseract", value, has_pct))
            except Exception as exc:
                attempts.append({"engine": "tesseract", "mode": mode, "psm": psm, "error": str(exc)})

    try:
        rapid_results, _ = rapid_engine(np.asarray(crop.convert("RGB")))
        if rapid_results:
            for item in rapid_results:
                raw = str(item[1]).strip()
                score = float(item[2]) if len(item) > 2 else 0.0
                value, has_pct = parse_percent(raw)
                attempts.append({
                    "engine": "rapidocr", "raw": raw, "score": score,
                    "value": value, "has_percent": has_pct,
                })
                if value is not None and score >= 0.35 and (value >= 10 or has_pct):
                    votes.append(("rapidocr", value, has_pct))
    except Exception as exc:
        attempts.append({"engine": "rapidocr", "error": str(exc)})

    counts, engine_votes, percent_sign = {}, {}, {}
    for engine, value, has_pct in votes:
        counts[value] = counts.get(value, 0) + 1
        engine_votes.setdefault(value, set()).add(engine)
        percent_sign[value] = percent_sign.get(value, False) or has_pct

    ranked = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked:
        return None, {"crop": list(box), "votes": counts, "attempts": attempts, "reason": "no_valid_reading"}

    best, count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0
    # Require at least two matching readings and no tie. Single-digit values
    # must include the % sign, preventing clipped "100%" from becoming "1".
    if count < 2 or count == second_count or (best < 10 and not percent_sign.get(best, False)):
        return None, {"crop": list(box), "votes": counts, "attempts": attempts, "reason": "ambiguous"}

    return best, {
        "crop": list(box), "votes": counts,
        "engines": {str(k): sorted(v) for k, v in engine_votes.items()},
        "attempts": attempts, "accepted_votes": count,
    }

def main():
    DEBUG.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from rapidocr_onnxruntime import RapidOCR
    rapid_engine = RapidOCR()
    response, image = download_official_map()
    image.save(DEBUG / "cwa-source.png")

    aspect = image.width / image.height
    if abs(aspect - REFERENCE_SIZE[0] / REFERENCE_SIZE[1]) > 0.12:
        raise RuntimeError(
            f"官方機率圖版型比例已改變（目前 {image.width}x{image.height}），"
            "停止使用舊座標，避免把百分比配錯縣市。"
        )

    values, details = {}, {}
    for city, roi in PERCENT_ROIS.items():
        percent, detail = read_percent_from_roi(image, city, roi, rapid_engine)
        details[city] = detail
        if percent is not None:
            values[city] = {"percent": percent}
            print(f"OCR：{city} {percent}%（一致性檢查通過）")
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
            "details": details,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if not values:
        raise RuntimeError("沒有數值通過 OCR 一致性檢查；保留診斷資料，未更新正式 JSON。")

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
        "ocr_note": "僅發布通過 OCR 一致性檢查的數值；無法可靠辨識的地點不發布機率。",
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"已更新 {OUT}：可靠辨識 {len(values)}/{len(PERCENT_ROIS)} 個地點")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

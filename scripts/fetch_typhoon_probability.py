#!/usr/bin/env python3
"""Extract per-location 120-hour typhoon probabilities from the CWA official image.

This version corrects the percentage ROI table using the actual 400x500 CWA
image in the uploaded diagnostics. It uses RapidOCR plus several Tesseract
passes, and omits uncertain values rather than inventing them.
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

# Exact percentage-text crops measured against the official 400x500 CWA image.
# Coordinates: left, top, right, bottom. Do not use city-label coordinates.
PERCENT_ROIS = {
    "連江縣": (119, 129, 152, 148),  # 77%
    "金門縣": (99, 188, 123, 206),   # 1%
    "澎湖縣": (95, 248, 126, 269),   # 50%
    "新北市": (232, 105, 267, 126),  # 100%
    "基隆市": (305, 94, 342, 115),   # 100%
    "桃園市": (212, 132, 249, 154),  # 100%
    "臺北市": (314, 131, 355, 154),  # 100%
    "新竹市": (174, 159, 214, 181),  # 100%
    "新竹縣": (234, 176, 271, 199),  # 100%
    "苗栗縣": (161, 190, 201, 213),  # 100%
    "臺中市": (220, 215, 254, 237),  # 99%
    "彰化縣": (75, 237, 107, 259),   # 99%
    "南投縣": (206, 248, 244, 271),  # 99%
    "雲林縣": (137, 270, 176, 294),  # 99%
    "嘉義市": (190, 300, 221, 325),  # 98%
    "嘉義縣": (106, 312, 137, 338),  # 96%
    "臺南市": (105, 359, 136, 385),  # 86%
    "高雄市": (109, 393, 140, 420),  # 79%
    "屏東縣": (212, 380, 251, 405),  # 95%
    "宜蘭縣": (320, 162, 362, 187),  # 100%
    "花蓮縣": (303, 238, 343, 263),  # 100%
    "臺東縣": (227, 358, 265, 384),  # 100%
    "綠島": (300, 350, 338, 375),    # 100%
    "蘭嶼": (304, 422, 345, 447),    # 100%
    "恆春": (188, 453, 216, 478),    # 98%
}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def download_official_map():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/6.0)",
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
        # Compatibility fallback for the CWA certificate-chain issue.
        # Redirects are validated below; only the official CWA host is accepted.
        print(f"警告：官方圖 SSL 驗證失敗，使用官方網域限定的備援下載：{err}", file=sys.stderr)
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

def image_variants(crop):
    # Keep the original crop as well as enhanced variants. Avoid color masks
    # that can accidentally erase thin digits or the percent sign.
    enlarged = crop.resize((crop.width * 10, crop.height * 10), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(enlarged)
    gray = ImageEnhance.Contrast(gray).enhance(2.4)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    auto = ImageOps.autocontrast(gray).filter(ImageFilter.MedianFilter(size=3))
    threshold = gray.point(lambda px: 255 if px > 170 else 0)
    return [("gray", gray), ("auto", auto), ("threshold", threshold)]

def parse_percent(text):
    if not text:
        return None, False
    cleaned = text.replace("％", "%").replace(" ", "").replace("\n", "").replace("\r", "")
    # Require a complete percentage when a one-digit value is detected.
    match = re.search(r"(?<!\d)(100|[1-9]?\d)\s*(%)?", cleaned)
    if not match:
        return None, False
    number = int(match.group(1))
    has_percent = match.group(2) == "%"
    if not 0 <= number <= 100:
        return None, has_percent
    return number, has_percent

def read_percent_from_roi(image, city, roi, rapid_engine):
    box = normalize_roi(roi, image.size)
    crop = image.crop(box)
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", city)
    crop.save(DEBUG / f"roi-{safe_name}.png")

    # Each engine/transform is a separate vote. Store every raw result for audit.
    votes, attempts = [], []
    for mode, processed in image_variants(crop):
        for psm in (7, 8, 13):
            try:
                raw = pytesseract.image_to_string(
                    processed, lang="eng",
                    config=f"--oem 1 --psm {psm} -c tessedit_char_whitelist=0123456789%",
                ).strip()
                value, has_pct = parse_percent(raw)
                attempts.append({"engine": "tesseract", "mode": mode, "psm": psm,
                                 "raw": raw, "value": value, "has_percent": has_pct})
                if value is not None:
                    # Values 0-9 are accepted only if the % sign was read.
                    if value >= 10 or has_pct:
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
                attempts.append({"engine": "rapidocr", "raw": raw, "score": score,
                                 "value": value, "has_percent": has_pct})
                if value is not None and score >= 0.35 and (value >= 10 or has_pct):
                    votes.append(("rapidocr", value, has_pct))
    except Exception as exc:
        attempts.append({"engine": "rapidocr", "error": str(exc)})

    counts = {}
    engines_by_value = {}
    percent_sign_by_value = {}
    for engine, value, has_pct in votes:
        counts[value] = counts.get(value, 0) + 1
        engines_by_value.setdefault(value, set()).add(engine)
        percent_sign_by_value[value] = percent_sign_by_value.get(value, False) or has_pct

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, {"crop": list(box), "votes": counts, "attempts": attempts, "reason": "no_valid_reading"}

    best, count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0
    # For one-digit values, require the % sign and agreement; otherwise a clipped
    # 100 can be mistaken for 1. For all values require a strict consensus.
    valid = count >= 2 and count > second_count
    if best < 10 and not percent_sign_by_value.get(best, False):
        valid = False
    if not valid:
        return None, {"crop": list(box), "votes": counts, "attempts": attempts, "reason": "ambiguous"}
    return best, {
        "crop": list(box), "votes": counts,
        "engines": {str(k): sorted(v) for k, v in engines_by_value.items()},
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
    expected_aspect = REFERENCE_SIZE[0] / REFERENCE_SIZE[1]
    if abs(aspect - expected_aspect) > 0.12:
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
            print(f"OCR：{city} {percent}%（多引擎一致）")
        else:
            print(f"OCR：{city} 未能可靠辨識，保留 --")

    diagnostics = {
        "source_url": IMAGE_URL,
        "downloaded_at": utc_now(),
        "image_size": {"width": image.width, "height": image.height},
        "recognized_count": len(values),
        "expected_count": len(PERCENT_ROIS),
        "values": values,
        "details": details,
    }
    (DEBUG / "ocr-diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not values:
        raise RuntimeError("沒有數值通過 OCR 一致性檢查；已保留診斷資料，未更新正式 JSON。")

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
        "ocr_note": "僅發布通過多引擎一致性檢查的機率；有歧義的地點不發布數值。",
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

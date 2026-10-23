#!/usr/bin/env python3
"""Extract CWA 120-hour typhoon probabilities from the official WSP_CH.png.

The ROI table is calibrated to the 400x500 official source image found in the
latest uploaded OCR diagnostics. Uncertain readings are omitted, never guessed.
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

# Full percentage label boxes measured from the official 400x500 source image.
# Coordinates: (left, top, right, bottom). They target numbers, not city names.
PERCENT_ROIS = {
    "連江縣": (121, 130, 151, 147),
    "金門縣": (87, 192, 119, 210),
    "澎湖縣": (94, 247, 126, 266),
    "新北市": (234, 102, 269, 124),
    "基隆市": (308, 95, 344, 113),
    "桃園市": (215, 132, 253, 155),
    "臺北市": (314, 132, 354, 155),
    "新竹市": (168, 160, 211, 184),
    "新竹縣": (235, 177, 272, 198),
    "苗栗縣": (164, 192, 200, 213),
    "臺中市": (223, 216, 253, 235),
    "彰化縣": (156, 236, 187, 256),
    "南投縣": (233, 247, 266, 269),
    "雲林縣": (153, 267, 184, 289),
    "嘉義市": (207, 301, 240, 324),
    "嘉義縣": (108, 309, 140, 332),
    "臺南市": (105, 358, 136, 379),
    "高雄市": (107, 399, 141, 423),
    "屏東縣": (207, 381, 243, 405),
    "宜蘭縣": (315, 164, 355, 188),
    "花蓮縣": (314, 240, 358, 264),
    "臺東縣": (229, 359, 267, 382),
    "綠島": (300, 354, 335, 376),
    "蘭嶼": (302, 427, 343, 451),
    "恆春": (182, 454, 220, 477),
}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def download_official_map():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/8.0)",
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
                attempts.append({"engine": "tesseract", "mode": mode, "psm": psm,
                                 "raw": raw, "value": value, "has_percent": has_pct})
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
                attempts.append({"engine": "rapidocr", "raw": raw, "score": score,
                                 "value": value, "has_percent": has_pct})
                if value is not None and score >= 0.35 and (value >= 10 or has_pct):
                    votes.append(("rapidocr", value, has_pct))
    except Exception as exc:
        attempts.append({"engine": "rapidocr", "error": str(exc)})

    # 金門的「1%」字樣很容易被 Tesseract 把百分號誤讀成「70」，
    # 因而多數票錯成 17%。若 RapidOCR 有清楚讀到含 % 的數值，
    # 金門優先採用該讀值，避免被錯誤的 Tesseract 多數票覆蓋。
    if city == "金門縣":
        rapid_percent_reads = [
            a for a in attempts
            if a.get("engine") == "rapidocr"
            and a.get("value") is not None
            and a.get("has_percent")
            and a.get("score", 0) >= 0.35
        ]
        if rapid_percent_reads:
            best_rapid = max(rapid_percent_reads, key=lambda a: a.get("score", 0))
            return best_rapid["value"], {
                "crop": list(box),
                "votes": {str(best_rapid["value"]): 1},
                "engines": {str(best_rapid["value"]): ["rapidocr-priority-for-jinmen"]},
                "attempts": attempts,
                "accepted_votes": 1,
                "reason": "jinmen_percent_sign_reading_prioritized",
            }

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
    # One-digit readings must include the percent sign so a clipped 100 cannot
    # become 1. For all values, require a clear majority with no tie.
    total_votes = sum(counts.values())
    if count < 2 or count == second_count or count / total_votes < 0.65:
        return None, {"crop": list(box), "votes": counts, "attempts": attempts, "reason": "ambiguous"}
    if best < 10 and not percent_sign.get(best, False):
        return None, {"crop": list(box), "votes": counts, "attempts": attempts, "reason": "single_digit_without_percent_sign"}

    return best, {"crop": list(box), "votes": counts,
                  "engines": {str(k): sorted(v) for k, v in engine_votes.items()},
                  "attempts": attempts, "accepted_votes": count}

def main():
    DEBUG.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    from rapidocr_onnxruntime import RapidOCR
    rapid_engine = RapidOCR()

    response, image = download_official_map()
    image.save(DEBUG / "cwa-source.png")
    aspect = image.width / image.height
    if abs(aspect - REFERENCE_SIZE[0] / REFERENCE_SIZE[1]) > 0.12:
        raise RuntimeError(f"官方圖版型比例改變（{image.width}x{image.height}），停止使用舊座標。")

    values, details = {}, {}
    for city, roi in PERCENT_ROIS.items():
        percent, detail = read_percent_from_roi(image, city, roi, rapid_engine)
        details[city] = detail
        if percent is not None:
            values[city] = {"percent": percent}
            print(f"OCR：{city} {percent}%")
        else:
            print(f"OCR：{city} 未能可靠辨識，保留 --")

    (DEBUG / "ocr-diagnostics.json").write_text(json.dumps({
        "source_url": IMAGE_URL, "downloaded_at": utc_now(),
        "image_size": {"width": image.width, "height": image.height},
        "recognized_count": len(values), "expected_count": len(PERCENT_ROIS),
        "values": values, "details": details,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not values:
        raise RuntimeError("沒有數值通過 OCR 一致性檢查；未更新正式 JSON。")

    payload = {
        "source": "中央氣象署官方 120 小時颱風暴風侵襲機率圖",
        "source_url": IMAGE_URL, "generated_at": utc_now(),
        "source_last_modified": response.headers.get("Last-Modified"),
        "image_width": image.width, "image_height": image.height,
        "values": values, "recognized_count": len(values),
        "expected_count": len(PERCENT_ROIS),
        "ocr_note": "只發布通過 OCR 一致性檢查的數值；無法可靠辨識的地點不發布機率。",
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

#!/usr/bin/env python3
"""Safely extract CWA 120-hour typhoon probabilities from the official map.

Important: this is OCR of a raster image, not a structured official API.
Uncertain or conflicting OCR results are omitted rather than guessed.
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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "typhoon-probability.json"
DEBUG = ROOT / "artifacts" / "typhoon-probability-debug"
IMAGE_URL = "https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png"
REFERENCE_SIZE = (400, 500)

# Verified against the official 400x500 image included in the uploaded
# diagnostics ZIP. Each rectangle targets the orange percentage text itself.
# Coordinates: left, top, right, bottom.
PERCENT_ROIS = {
    "連江縣": (160, 123, 205, 153),
    "金門縣": (126, 184, 160, 212),
    "澎湖縣": (96, 243, 146, 272),
    "新北市": (230, 104, 277, 134),
    "基隆市": (304, 90, 352, 120),
    "桃園市": (216, 125, 265, 157),
    "臺北市": (312, 125, 360, 157),
    "新竹市": (170, 157, 216, 192),
    "新竹縣": (237, 172, 286, 205),
    "苗栗縣": (155, 182, 207, 215),
    "臺中市": (223, 207, 272, 240),
    "彰化縣": (143, 226, 190, 258),
    "南投縣": (235, 243, 285, 277),
    "雲林縣": (156, 263, 206, 296),
    "嘉義市": (213, 307, 262, 341),
    "嘉義縣": (132, 306, 180, 341),
    "臺南市": (100, 355, 150, 393),
    "高雄市": (100, 405, 153, 444),
    "屏東縣": (222, 376, 272, 412),
    "宜蘭縣": (319, 157, 369, 191),
    "花蓮縣": (311, 230, 364, 266),
    "臺東縣": (228, 357, 279, 396),
    "綠島": (294, 345, 343, 382),
    "蘭嶼": (307, 416, 361, 455),
    "恆春": (186, 446, 237, 484),
}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def download_official_map():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/5.0)",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "image/png,image/*,*/*;q=0.8",
    }
    try:
        response = requests.get(
            IMAGE_URL,
            params={"_": int(datetime.now().timestamp())},
            timeout=(10, 35),
            headers=headers,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.exceptions.SSLError as err:
        print(f"警告：官方圖 SSL 驗證失敗，改用限定官方網域的備援下載：{err}", file=sys.stderr)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(
            IMAGE_URL,
            params={"_": int(datetime.now().timestamp())},
            timeout=(10, 35),
            headers=headers,
            allow_redirects=True,
            verify=False,
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
        max(0, round(x1 * sx)),
        max(0, round(y1 * sy)),
        min(size[0], round(x2 * sx)),
        min(size[1], round(y2 * sy)),
    )

def orange_text_mask(crop):
    """Keep orange percentage glyphs; remove most of the cyan map/background."""
    import numpy as np
    rgb = np.asarray(crop.convert("RGB"))
    r, g, b = rgb[:, :, 0].astype("int16"), rgb[:, :, 1].astype("int16"), rgb[:, :, 2].astype("int16")
    orange = (r > 105) & ((r - g) > 22) & ((r - b) > 28) & (g < 220)
    # OCR expects dark glyphs on a white background.
    return Image.fromarray((~orange * 255).astype("uint8"))

def make_variants(crop):
    enlarged = crop.resize((crop.width * 8, crop.height * 8), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(enlarged)
    gray = ImageEnhance.Contrast(gray).enhance(2.5)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    auto = ImageOps.autocontrast(gray).filter(ImageFilter.MedianFilter(size=3))
    variants = [
        ("gray", gray),
        ("auto", auto),
        ("threshold", gray.point(lambda px: 255 if px > 170 else 0)),
        ("orange-mask", orange_text_mask(crop).resize((crop.width * 8, crop.height * 8), Image.Resampling.NEAREST)),
    ]
    return variants

def read_percent_from_roi(image, city, roi):
    box = normalize_roi(roi, image.size)
    crop = image.crop(box)
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", city)
    crop.save(DEBUG / f"roi-{safe}.png")

    attempts = []
    for mode, processed in make_variants(crop):
        for psm in (7, 8, 13):
            try:
                raw = pytesseract.image_to_string(
                    processed,
                    lang="eng",
                    config=f"--oem 1 --psm {psm} -c tessedit_char_whitelist=0123456789%",
                ).strip()
            except Exception as exc:
                attempts.append({"mode": mode, "psm": psm, "raw": "", "error": str(exc)})
                continue

            cleaned = raw.replace("％", "%").replace(" ", "").replace("\n", "")
            # A percent sign is required for one-digit values such as 1%;
            # this prevents a clipped "100%" from being published as "1".
            match = re.search(r"(?<!\d)(100|[1-9]?\d)%", cleaned)
            if match:
                value = int(match.group(1))
                attempts.append({"mode": mode, "psm": psm, "raw": raw, "value": value})

    counts = {}
    for item in attempts:
        if "value" in item:
            counts[item["value"]] = counts.get(item["value"], 0) + 1

    ranked = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked:
        return None, {"crop": list(box), "counts": counts, "attempts": attempts, "reason": "no_percent_value"}

    best_value, best_count = ranked[0]
    total_hits = sum(counts.values())
    second_count = ranked[1][1] if len(ranked) > 1 else 0

    # Do not choose a plurality when OCR disagrees. Require at least 3 hits,
    # a strict winner, and >=70% of all numeric OCR hits to agree.
    if best_count < 3 or best_count == second_count or best_count / total_hits < 0.70:
        return None, {
            "crop": list(box), "counts": counts, "attempts": attempts,
            "reason": "conflicting_or_low_consensus",
        }

    return best_value, {
        "crop": list(box), "counts": counts, "attempts": attempts,
        "accepted_count": best_count, "total_hits": total_hits,
    }

def main():
    DEBUG.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    response, image = download_official_map()
    image.save(DEBUG / "cwa-source.png")

    aspect = image.width / image.height
    expected_aspect = REFERENCE_SIZE[0] / REFERENCE_SIZE[1]
    if abs(aspect - expected_aspect) > 0.12:
        raise RuntimeError(
            f"官方機率圖版型比例已改變（目前 {image.width}x{image.height}），"
            "停止使用舊座標，避免把百分比配錯縣市。"
        )

    values, diagnostics = {}, {}
    for city, roi in PERCENT_ROIS.items():
        percent, detail = read_percent_from_roi(image, city, roi)
        diagnostics[city] = detail
        if percent is not None:
            values[city] = {"percent": percent}
            print(f"OCR：{city} {percent}%（嚴格一致性檢查通過）")
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
        raise RuntimeError("沒有任何數值通過嚴格 OCR 檢查；未更新正式 JSON。")

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
        "ocr_note": "只發布通過嚴格 OCR 一致性檢查的數值；有歧義的地點不發布數值，避免錯誤機率。",
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

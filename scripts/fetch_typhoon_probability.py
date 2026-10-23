#!/usr/bin/env python3
"""Read the CWA 120-hour typhoon probability map without publishing guessed values."""
import json
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageOps, ImageEnhance, ImageDraw
import pytesseract
from pytesseract import Output

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "typhoon-probability.json"
DEBUG = ROOT / "artifacts" / "typhoon-probability-debug"
IMAGE_URL = "https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png"
CITIES = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市",
    "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣",
    "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣", "綠島", "蘭嶼", "恆春"
]

def clean_text(value):
    text = re.sub(r"[\s·•,，。:：;；]", "", str(value or "")).replace("台", "臺")
    # Normalize common short labels and OCR confusions before matching.
    text = text.replace("速江縣", "連江縣").replace("亳南市", "臺南市").replace("亳南", "臺南")
    text = re.sub(r"澎湖(?!縣)", "澎湖縣", text)
    text = re.sub(r"苗栗(?!縣)", "苗栗縣", text)
    text = re.sub(r"宜蘭(?!縣)", "宜蘭縣", text)
    text = text.replace("恒春", "恆春")
    return text

def box_center(box):
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

def main():
    DEBUG.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/3.0)",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "image/png,image/*,*/*;q=0.8",
    }
    params = {"_": int(datetime.now().timestamp())}
    try:
        response = requests.get(
            IMAGE_URL, timeout=(10, 35), headers=headers, params=params,
            allow_redirects=True
        )
        response.raise_for_status()
    except requests.exceptions.SSLError as err:
        print(f"警告：CWA 圖片憑證驗證失敗，改用限定官方網址備援：{err}", file=sys.stderr)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(
            IMAGE_URL, timeout=(10, 35), headers=headers, params=params,
            allow_redirects=True, verify=False
        )
        response.raise_for_status()

    host = (requests.utils.urlparse(response.url).hostname or "").lower()
    if host not in ("www.cwa.gov.tw", "cwa.gov.tw"):
        raise RuntimeError(f"下載重新導向到非氣象署網域，停止：{response.url}")
    if not response.headers.get("content-type", "").lower().startswith("image/"):
        raise RuntimeError(f"氣象署回傳不是圖片：{response.headers.get('content-type')}")

    image = Image.open(BytesIO(response.content)).convert("RGB")
    if image.width < 200 or image.height < 200:
        raise RuntimeError(f"官方圖尺寸異常：{image.size}")
    image.save(DEBUG / "cwa-source.png")

    city_pattern = re.compile("|".join(re.escape(c) for c in sorted(CITIES, key=len, reverse=True)))
    percent_pattern = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*[%％]")
    # Keep detections from each pass separate; never pair a number with a distant city.
    candidates = {city: [] for city in CITIES}
    ocr_records = []

    def process_hits(city_hits, percent_hits, pass_name):
        # A tight spatial gate avoids the old 95x65px search that paired
        # Penghu's 42% with Changhua and Lienchiang's 80% with Hsinchu City.
        pairs = []
        for city, cx, cy, city_conf, city_token in city_hits:
            for percent, px, py, pct_conf, pct_token in percent_hits:
                dx, dy = abs(cx - px), abs(cy - py)
                if dx > 50 or dy > 26:
                    continue
                # Prefer numbers found in the same OCR token, then nearby same-row values.
                same_token = city_token == pct_token
                dist = (dx * dx + (dy * 1.5) ** 2) ** 0.5
                score = min(city_conf, pct_conf) + (35 if same_token else 0) - dist
                pairs.append((score, city, int(percent), px, py, pass_name, same_token))
        pairs.sort(reverse=True, key=lambda item: item[0])
        used_city, used_percent = set(), set()
        for score, city, percent, px, py, pass_name, same_token in pairs:
            pkey = (round(px / 8), round(py / 8), percent)
            if city in used_city or pkey in used_percent:
                continue
            used_city.add(city)
            used_percent.add(pkey)
            candidates[city].append({
                "percent": percent, "score": score, "x": px, "y": py,
                "pass": pass_name, "same_token": same_token
            })

    # RapidOCR is useful for Chinese text; only accept same-token or close same-row pairs.
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        rapid = RapidOCR()
        result, _ = rapid(np.array(image))
        city_hits, percent_hits = [], []
        for index, row in enumerate(result or []):
            if len(row) < 3:
                continue
            box, raw_text, raw_conf = row[0], str(row[1] or ""), row[2]
            text = clean_text(raw_text)
            cx, cy = box_center(box)
            conf = max(0.0, min(100.0, float(raw_conf) * 100))
            ocr_records.append({
                "engine": "rapidocr", "text": text, "confidence": conf,
                "box": [[float(p[0]), float(p[1])] for p in box]
            })
            for match in city_pattern.finditer(text):
                city = clean_text(match.group(0))
                if city in CITIES:
                    city_hits.append((city, cx, cy, conf, index))
            for match in percent_pattern.finditer(text):
                percent_hits.append((int(match.group(1)), cx, cy, conf, index))
        process_hits(city_hits, percent_hits, "rapidocr")
        print(f"RapidOCR：辨識到 {len(city_hits)} 個地點標籤、{len(percent_hits)} 個百分比標籤")
    except Exception as err:
        print(f"RapidOCR 無法執行：{err}", file=sys.stderr)

    # Tesseract fallback: use original-image coordinates after resizing for OCR.
    scale = 4
    enlarged = image.resize((image.width * scale, image.height * scale))
    gray = ImageOps.grayscale(enlarged)
    gray = ImageEnhance.Contrast(gray).enhance(2.2)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    variants = [
        ("gray", gray),
        ("threshold", gray.point(lambda value: 255 if value > 155 else 0)),
        ("autocontrast", ImageOps.autocontrast(gray)),
    ]
    for variant_name, processed in variants:
        for psm in (11, 12, 6, 4):
            data = pytesseract.image_to_data(
                processed, lang="chi_tra+eng", config=f"--oem 1 --psm {psm}",
                output_type=Output.DICT
            )
            city_hits, percent_hits = [], []
            for i, raw in enumerate(data.get("text", [])):
                raw = (raw or "").strip()
                if not raw:
                    continue
                try:
                    conf = float(data["conf"][i])
                except (TypeError, ValueError):
                    continue
                if conf < 0:
                    continue
                left = float(data["left"][i]) / scale
                top = float(data["top"][i]) / scale
                width = float(data["width"][i]) / scale
                height = float(data["height"][i]) / scale
                x, y = left + width / 2, top + height / 2
                text = clean_text(raw)
                token_id = f"tess-{variant_name}-{psm}-{i}"
                ocr_records.append({
                    "engine": "tesseract", "text": text, "confidence": conf,
                    "x": x, "y": y, "variant": variant_name, "psm": psm
                })
                for match in city_pattern.finditer(text):
                    city = clean_text(match.group(0))
                    if city in CITIES:
                        city_hits.append((city, x, y, conf, token_id))
                for match in percent_pattern.finditer(text):
                    percent_hits.append((int(match.group(1)), x, y, conf, token_id))
            process_hits(city_hits, percent_hits, f"tesseract/{variant_name}/psm{psm}")

    # Resolve candidates only when the best value is not contradicted by a close-scoring candidate.
    values = {}
    ambiguities = {}
    for city, options in candidates.items():
        if not options:
            continue
        options.sort(key=lambda item: item["score"], reverse=True)
        best = options[0]
        competing = [o for o in options[1:] if o["percent"] != best["percent"] and best["score"] - o["score"] < 8]
        if competing:
            ambiguities[city] = [
                {"percent": o["percent"], "score": round(o["score"], 2), "pass": o["pass"]}
                for o in [best] + competing
            ]
            continue
        values[city] = {
            "percent": best["percent"],
            "evidence": {"pass": best["pass"], "same_token": best["same_token"]}
        }

    diagnostic = {
        "source_url": IMAGE_URL,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "image_size": {"width": image.width, "height": image.height},
        "recognized_city_percent_pairs": values,
        "ambiguous_cities": ambiguities,
        "ocr_records": ocr_records,
    }
    # Correct JSON newline: write an actual newline, not the literal two-character sequence "\\n".
    (DEBUG / "ocr-diagnostics.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    for record in ocr_records:
        if record.get("engine") == "rapidocr" and record.get("box"):
            draw.polygon([tuple(p) for p in record["box"]], outline="red", width=1)
    for city, item in values.items():
        x, y = float(next((o["x"] for o in candidates[city] if o["percent"] == item["percent"]), 0)), \
               float(next((o["y"] for o in candidates[city] if o["percent"] == item["percent"]), 0))
        draw.ellipse((x-3, y-3, x+3, y+3), outline="blue", width=2)
    overlay.save(DEBUG / "ocr-overlay.png")

    print(f"OCR：辨識到 {len(values)}/{len(CITIES)} 個地點；歧義 {len(ambiguities)} 個")
    if ambiguities:
        print("有歧義的地點：" + json.dumps(ambiguities, ensure_ascii=False))
    
# 只發布有明確辨識證據的官方數值。
# 未辨識或有歧義的地點不發布，不猜測機率。
if not values:
    raise RuntimeError(
        f"OCR 沒有辨識到任何可安全發布的官方機率；"
        f"歧義 {len(ambiguities)} 個。"
    )

    payload = {
        "source": "中央氣象署官方 120 小時颱風暴風侵襲機率圖",
        "source_url": IMAGE_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_last_modified": response.headers.get("Last-Modified"),
        "image_width": image.width,
        "image_height": image.height,
        "values": {city: {"percent": item["percent"]} for city, item in values.items()},
        "ocr_note": "僅在所有地點均有無歧義的官方圖 OCR 證據時更新。"
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"已更新 {OUT}: {len(values)}/{len(CITIES)} 個地點")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

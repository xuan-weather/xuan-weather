#!/usr/bin/env python3
"""Fetch the CWA official 120-hour typhoon probability map and extract exact city percentages.
Values are only published when recognized from the current official graphic; no values are guessed.
"""
import json, re, sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageOps, ImageEnhance
import pytesseract
from pytesseract import Output

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "typhoon-probability.json"
IMAGE_URL = "https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png"
CITIES = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市",
    "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣",
    "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣", "綠島", "蘭嶼", "恆春"
]

def clean_text(value):
    return re.sub(r"[\s·•,，。:：;；]", "", str(value or "")).replace("台", "臺")

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/2.0)",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "image/avif,image/webp,image/apng,image/png,image/*,*/*;q=0.8",
    }
    params = {"_": int(datetime.now().timestamp())}
    try:
        r = requests.get(IMAGE_URL, timeout=(10, 35), headers=headers, params=params,
                         allow_redirects=True)
        r.raise_for_status()
    except requests.exceptions.SSLError as ssl_error:
        print(f"警告：氣象署圖片 HTTPS 憑證驗證失敗，啟用限定網址備援：{ssl_error}", file=sys.stderr)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(IMAGE_URL, timeout=(10, 35), headers=headers, params=params,
                         allow_redirects=True, verify=False)
        r.raise_for_status()

    final_host = (requests.utils.urlparse(r.url).hostname or "").lower()
    if final_host not in ("www.cwa.gov.tw", "cwa.gov.tw"):
        raise RuntimeError(f"下載被重新導向至非氣象署網域，停止處理：{r.url}")
    if not r.headers.get("content-type", "").lower().startswith("image/"):
        raise RuntimeError(f"官方圖回傳不是圖片：{r.headers.get('content-type')}")

    image = Image.open(BytesIO(r.content)).convert("RGB")
    if image.width < 200 or image.height < 200:
        raise RuntimeError(f"官方圖尺寸異常：{image.size}")

    city_regex = re.compile("|".join(re.escape(c) for c in sorted(CITIES, key=len, reverse=True)))
    percent_regex = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*[%％]")
    detections = {}

    def associate(city_hits, percent_hits, pass_name):
        pairs = []
        for city, cx, cy, cc in city_hits:
            for percent, px, py, pc in percent_hits:
                dx, dy = abs(cx-px), abs(cy-py)
                if dx > max(95, image.width*0.12) or dy > max(65, image.height*0.12):
                    continue
                dist = (dx*dx + (dy*1.35)**2) ** 0.5
                pairs.append((dist, city, percent, min(cc, pc), px, py, pass_name))
        pairs.sort(key=lambda p: (p[0], -p[3]))
        used_cities, used_percent = set(), set()
        for dist, city, percent, conf, px, py, pass_name in pairs:
            pkey = (percent, round(px/12), round(py/12))
            if city in used_cities or pkey in used_percent:
                continue
            used_cities.add(city)
            used_percent.add(pkey)
            score = conf - dist*0.12
            if city not in detections or score > detections[city]["score"]:
                detections[city] = {
                    "percent": int(percent), "score": score, "x": px, "y": py, "pass": pass_name
                }

    def dedupe(items):
        out = {}
        for item in items:
            key = (item[0], round(item[1]/8), round(item[2]/8))
            if key not in out or item[3] > out[key][3]:
                out[key] = item
        return list(out.values())

    # Tesseract pass: several preprocessing and layout modes.
    scale = 4
    enlarged = image.resize((image.width * scale, image.height * scale))
    gray = ImageOps.grayscale(enlarged)
    gray = ImageEnhance.Contrast(gray).enhance(2.2)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    variants = [
        ("gray", gray),
        ("threshold", gray.point(lambda v: 255 if v > 155 else 0)),
        ("autocontrast", ImageOps.autocontrast(gray)),
    ]
    for variant_name, processed in variants:
        for psm in (11, 12, 6, 4):
            data = pytesseract.image_to_data(
                processed, lang="chi_tra+eng", config=f"--oem 1 --psm {psm}",
                output_type=Output.DICT
            )
            n = len(data.get("text", []))
            lines, tokens = {}, []
            for i in range(n):
                raw = (data["text"][i] or "").strip()
                if not raw:
                    continue
                try:
                    conf = float(data["conf"][i])
                except (TypeError, ValueError):
                    conf = -1
                if conf < 0:
                    continue
                x = float(data["left"][i]) / scale
                y = float(data["top"][i]) / scale
                w = float(data["width"][i]) / scale
                h = float(data["height"][i]) / scale
                token = {
                    "text": clean_text(raw), "x": x+w/2, "y": y+h/2,
                    "left": x, "right": x+w, "conf": conf
                }
                tokens.append(token)
                key = (
                    data.get("page_num", [1]*n)[i], data.get("block_num", [0]*n)[i],
                    data.get("par_num", [0]*n)[i], data.get("line_num", [0]*n)[i]
                )
                lines.setdefault(key, []).append(token)

            city_hits, percent_hits = [], []
            for token in tokens:
                for match in city_regex.finditer(token["text"]):
                    city = clean_text(match.group(0))
                    if city in CITIES:
                        city_hits.append((city, token["x"], token["y"], token["conf"]))
                for match in percent_regex.finditer(token["text"]):
                    percent_hits.append((int(match.group(1)), token["x"], token["y"], token["conf"]))

            for row in lines.values():
                row.sort(key=lambda t: t["left"])
                txt = "".join(t["text"] for t in row)
                positions = []
                for token in row:
                    chars = max(1, len(token["text"]))
                    for j in range(chars):
                        positions.append((
                            token["left"] + (token["right"]-token["left"])*(j+0.5)/chars,
                            token["y"], token["conf"]
                        ))
                for match in city_regex.finditer(txt):
                    city = clean_text(match.group(0))
                    if city in CITIES and match.start() < len(positions):
                        x, y, conf = positions[match.start()]
                        city_hits.append((city, x, y, conf))
                for match in percent_regex.finditer(txt):
                    if match.start() < len(positions):
                        x, y, conf = positions[min((match.start()+match.end()-1)//2, len(positions)-1)]
                        percent_hits.append((int(match.group(1)), x, y, conf))
            associate(dedupe(city_hits), dedupe(percent_hits), f"{variant_name}/psm{psm}")

    # RapidOCR fallback: independent Chinese-capable OCR model with box-level coordinates.
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        rapid = RapidOCR()
        rapid_result, _elapsed = rapid(np.array(image))
        rapid_city_hits, rapid_percent_hits = [], []
        for row in (rapid_result or []):
            if len(row) < 3:
                continue
            box, raw_text, raw_score = row[0], str(row[1] or ""), row[2]
            text = clean_text(raw_text)
            if not text:
                continue
            try:
                conf = float(raw_score) * 100
            except (TypeError, ValueError):
                conf = 50.0
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            width = max(1.0, right-left)
            center_y = (top+bottom)/2
            for match in city_regex.finditer(text):
                city = clean_text(match.group(0))
                if city in CITIES:
                    x = left + width*((match.start()+match.end())/2)/max(1, len(text))
                    rapid_city_hits.append((city, x, center_y, conf))
            for match in percent_regex.finditer(text):
                x = left + width*((match.start()+match.end())/2)/max(1, len(text))
                rapid_percent_hits.append((int(match.group(1)), x, center_y, conf))
        associate(rapid_city_hits, rapid_percent_hits, "rapidocr/chinese")
        print(f"RapidOCR 診斷：辨識到 {len(rapid_city_hits)} 個地點標籤、{len(rapid_percent_hits)} 個百分比標籤")
    except Exception as rapid_error:
        print(f"警告：RapidOCR 備援未能執行：{rapid_error}", file=sys.stderr)

    values = {city: {"percent": item["percent"]} for city, item in detections.items()}
    print(f"OCR 診斷：辨識到 {len(values)}/{len(CITIES)} 個地點：" +
          ", ".join(f"{k}={v['percent']}%" for k, v in values.items()))

    # Fail closed unless nearly the complete city list was read. Never publish guessed values.
    minimum_required = len(CITIES)
    if len(values) < minimum_required:
        raise RuntimeError(
            f"官方圖 OCR 僅辨識 {len(values)}/{len(CITIES)} 個地點，未達完整清單門檻 {minimum_required}；"
            "不發布不完整新資料，也不填入推測值。"
        )

    payload = {
        "source": "中央氣象署官方 120 小時颱風暴風侵襲機率圖",
        "source_url": IMAGE_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_last_modified": r.headers.get("Last-Modified"),
        "image_width": image.width,
        "image_height": image.height,
        "values": values,
        "ocr_note": "百分比由官方圖 OCR 辨識；未能可靠辨識的地點不輸出。"
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"已更新 {OUT}: {len(values)}/{len(CITIES)} 個地點")
    print(json.dumps(values, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

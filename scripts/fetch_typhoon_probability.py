#!/usr/bin/env python3
"""Fetch CWA's official 120-hour typhoon probability map and cautiously OCR city percentages.
Only city/percentage pairs that pass conservative proximity checks are written to JSON.
"""
from datetime import datetime, timezone
from pathlib import Path
import json, re, sys
import requests
from PIL import Image
import pytesseract
from pytesseract import Output
from io import BytesIO

IMAGE_URL = "https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png"
OUT = Path("data/typhoon-probability.json")
CITIES = [
    "基隆市","臺北市","新北市","桃園市","新竹市","新竹縣","苗栗縣","臺中市",
    "彰化縣","南投縣","雲林縣","嘉義市","嘉義縣","臺南市","高雄市","屏東縣",
    "宜蘭縣","花蓮縣","臺東縣","澎湖縣","金門縣","連江縣","綠島","蘭嶼","恆春"
]
ALIASES = {"台北市":"臺北市","台中市":"臺中市","台南市":"臺南市","台東縣":"臺東縣"}
PERCENT_RE = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*[%％]")

def main():
    response = requests.get(IMAGE_URL, timeout=(15, 45), headers={"User-Agent":"XuanWeather/1.0 official product sync"})
    response.raise_for_status()
    if not response.headers.get("content-type","").lower().startswith("image/"):
        raise RuntimeError("氣象署回傳內容不是圖片，停止更新以免覆蓋有效資料。")
    image = Image.open(BytesIO(response.content)).convert("RGB")
    # Tesseract's traditional-Chinese language pack is installed by the workflow.
    data = pytesseract.image_to_data(image, lang="chi_tra+eng", config="--psm 11", output_type=Output.DICT)
    words = []
    for i, raw in enumerate(data["text"]):
        text = (raw or "").strip().replace("％", "%")
        try: conf = float(data["conf"][i])
        except (ValueError, TypeError): conf = -1
        if not text or conf < 25:
            continue
        x, y, w, h = (int(data[k][i]) for k in ("left","top","width","height"))
        words.append({"text":text,"x":x,"y":y,"w":w,"h":h,"cx":x+w/2,"cy":y+h/2,"conf":conf})

    city_boxes = []
    for word in words:
        normalized = word["text"].replace("台","臺")
        for city in CITIES:
            if city in normalized or normalized in (city, city.replace("臺","台")):
                city_boxes.append((city, word))
    # Handle OCR where a city name and percentage are emitted as one token.
    values = {}
    for city, word in city_boxes:
        matches = list(PERCENT_RE.finditer(word["text"]))
        if matches:
            vals = {int(m.group(1)) for m in matches}
            if len(vals) == 1:
                values[city] = {"percent": vals.pop(), "ocr_confidence": round(word["conf"],1), "method":"same-token"}
    percent_boxes = []
    for word in words:
        for match in PERCENT_RE.finditer(word["text"]):
            percent_boxes.append({"percent":int(match.group(1)), **word})
    # Match only mutually-nearest city and percent boxes; this reduces cross-city assignments.
    city_candidates = {}
    for city, box in city_boxes:
        if city in values:
            continue
        options = []
        for p in percent_boxes:
            if p["text"] == box["text"]:
                continue
            dx = abs(p["cx"] - box["cx"])
            dy = abs(p["cy"] - box["cy"])
            # City labels and percentages are adjacent on the official map. Exclude distant labels.
            if dx <= 240 and dy <= 95:
                distance = (dx*dx + (dy*1.8)**2) ** 0.5
                options.append((distance, p))
        options.sort(key=lambda x:x[0])
        if options:
            city_candidates[city] = options[0][1]
    percent_best_city = {}
    for city, p in city_candidates.items():
        d = abs(p["cx"] - next(b["cx"] for c,b in city_boxes if c == city)) + 1.8*abs(p["cy"] - next(b["cy"] for c,b in city_boxes if c == city))
        if p["text"] not in percent_best_city or d < percent_best_city[p["text"]][0]:
            percent_best_city[p["text"]] = (d, city, p)
    for city, p in city_candidates.items():
        if percent_best_city.get(p["text"], (None,None))[1] != city:
            continue
        city_box = next(b for c,b in city_boxes if c == city)
        # Require the percentage to be reasonably close to its label.
        if abs(p["cx"]-city_box["cx"]) <= 240 and abs(p["cy"]-city_box["cy"]) <= 95:
            values[city] = {"percent":p["percent"],"ocr_confidence":round(min(p["conf"],city_box["conf"]),1),"method":"nearest-label"}

    payload = {
        "source": IMAGE_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z"),
        "source_last_modified": response.headers.get("Last-Modified"),
        "image_dimensions": {"width": image.width, "height": image.height},
        "values": values,
        "recognized_count": len(values),
        "note": "Percentages are OCR-derived from the official CWA map. Missing values mean OCR could not confidently pair a label and percentage."
    }
    print(f"Official map fetched: {image.width}x{image.height}; OCR matched {len(values)}/{len(CITIES)} locations.")
    # Only replace the published JSON when enough city/value pairs were identified.
    # On a weak OCR run, exit non-zero and preserve the last committed JSON.
    if len(values) < 15:
        raise RuntimeError(f"OCR 只辨識到 {len(values)}/{len(CITIES)} 個地點，低於安全門檻 15；保留上一版 JSON，不發布不完整資料。")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp_out = OUT.with_suffix(".json.tmp")
    temp_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    temp_out.replace(OUT)

if __name__ == "__main__":
    main()

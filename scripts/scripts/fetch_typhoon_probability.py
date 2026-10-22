#!/usr/bin/env python3
"""Fetch CWA's official 120-hour typhoon probability map and cautiously OCR city percentages."""
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
import json
import re
import requests
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract
from pytesseract import Output

IMAGE_URL = "https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png"
OUT = Path("data/typhoon-probability.json")
CITIES = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市",
    "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣",
    "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣", "綠島", "蘭嶼", "恆春"
]

def normalize(text):
    return (text or "").replace("台", "臺").replace(" ", "").replace("\n", "").strip()

def main():
    import urllib3
    # Temporary workaround for the certificate-chain issue seen in the runner.
    # This is scoped to the fixed official CWA image URL below.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    response = requests.get(
        IMAGE_URL,
        timeout=(15, 45),
        headers={"User-Agent": "XuanWeather/1.0 official product sync"},
        verify=False
    )
    response.raise_for_status()
    if not response.headers.get("content-type", "").lower().startswith("image/"):
        raise RuntimeError("氣象署回傳內容不是圖片，停止更新以免覆蓋有效資料。")

    original = Image.open(BytesIO(response.content)).convert("RGB")
    # The official image is small (typically 400x500); upscale and enhance before OCR.
    scale = 4
    image = original.resize(
        (original.width * scale, original.height * scale),
        Image.Resampling.LANCZOS
    )
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(2.0)
    image = image.filter(ImageFilter.SHARPEN)

    # Run several page segmentation modes and keep the best token for each position.
    all_tokens = {}
    for psm in (11, 6, 12, 4):
        data = pytesseract.image_to_data(
            image, lang="chi_tra+eng", config=f"--psm {psm}",
            output_type=Output.DICT
        )
        for i, raw in enumerate(data["text"]):
            token = (raw or "").strip().replace("％", "%")
            if not token:
                continue
            try:
                confidence = float(data["conf"][i])
            except (ValueError, TypeError):
                confidence = -1
            if confidence < 5:
                continue
            x, y, w, h = (
                int(data[k][i]) / scale for k in ("left", "top", "width", "height")
            )
            item = {
                "text": token, "x": x, "y": y, "w": w, "h": h,
                "cx": x + w / 2, "cy": y + h / 2, "conf": confidence
            }
            key = (round(x / 3), round(y / 3), normalize(token))
            if key not in all_tokens or confidence > all_tokens[key]["conf"]:
                all_tokens[key] = item

    words = sorted(all_tokens.values(), key=lambda word: (word["cy"], word["x"]))
    print("OCR text preview:", " | ".join(word["text"] for word in words[:120]))

    # Group words by approximate row, then join fragments because Chinese labels can
    # be split into multiple OCR tokens.
    rows = []
    for word in words:
        row = next(
            (r for r in rows if abs(r["cy"] - word["cy"]) <= max(10, word["h"] * 0.8)),
            None
        )
        if row is None:
            row = {"cy": word["cy"], "words": []}
            rows.append(row)
        row["words"].append(word)
        row["cy"] = sum(item["cy"] for item in row["words"]) / len(row["words"])

    city_boxes = []
    for row in rows:
        row["words"].sort(key=lambda word: word["x"])
        for start in range(len(row["words"])):
            for end in range(start, min(len(row["words"]), start + 5)):
                group = row["words"][start:end + 1]
                joined = normalize("".join(item["text"] for item in group))
                for city in CITIES:
                    if city in joined:
                        left = min(item["x"] for item in group)
                        top = min(item["y"] for item in group)
                        right = max(item["x"] + item["w"] for item in group)
                        bottom = max(item["y"] + item["h"] for item in group)
                        box = {
                            "text": city, "x": left, "y": top, "w": right-left,
                            "h": bottom-top, "cx": (left+right)/2, "cy": row["cy"],
                            "conf": min(item["conf"] for item in group)
                        }
                        if not any(
                            old_city == city and abs(old_box["cx"]-box["cx"]) < 20
                            and abs(old_box["cy"]-box["cy"]) < 12
                            for old_city, old_box in city_boxes
                        ):
                            city_boxes.append((city, box))

    percent_boxes = []
    percent_pattern = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*[%％]")
    for word in words:
        for match in percent_pattern.finditer(word["text"]):
            percent_boxes.append({"percent": int(match.group(1)), **word})

    # Handle a number and its percent sign being split into adjacent tokens.
    for row in rows:
        row_words = row["words"]
        for i, word in enumerate(row_words[:-1]):
            if re.fullmatch(r"100|[1-9]?\d", word["text"].strip()) and \
                    row_words[i + 1]["text"].strip() in ("%", "％"):
                percent_boxes.append({"percent": int(word["text"]), **word})

    values = {}
    for city, box in city_boxes:
        candidates = []
        for percent in percent_boxes:
            dx = abs(percent["cx"] - box["cx"])
            dy = abs(percent["cy"] - box["cy"])
            if dx <= 240 and dy <= 95:
                distance = (dx * dx + (dy * 1.8) ** 2) ** 0.5
                candidates.append((distance, percent))
        candidates.sort(key=lambda candidate: candidate[0])
        if candidates:
            best = candidates[0][1]
            values[city] = {
                "percent": best["percent"],
                "ocr_confidence": round(min(box["conf"], best["conf"]), 1),
                "method": "enhanced-ocr-nearest-label"
            }

    payload = {
        "source": IMAGE_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_last_modified": response.headers.get("Last-Modified"),
        "image_dimensions": {"width": original.width, "height": original.height},
        "values": values,
        "recognized_count": len(values),
        "note": "Percentages are OCR-derived from the official CWA map. Missing values mean OCR could not confidently pair a label and percentage."
    }
    print(
        f"Official map fetched: {original.width}x{original.height}; "
        f"OCR matched {len(values)}/{len(CITIES)} locations."
    )
    print("Recognized values:", json.dumps(values, ensure_ascii=False))

    # Keep the safety gate. Do not publish partial/low-confidence OCR results.
    if len(values) < 15:
        raise RuntimeError(
            f"OCR 只辨識到 {len(values)}/{len(CITIES)} 個地點，低於安全門檻 15；"
            "保留上一版 JSON，不發布不完整資料。請查看上方 OCR text preview。"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp_out = OUT.with_suffix(".json.tmp")
    temp_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    temp_out.replace(OUT)

if __name__ == "__main__":
    main()

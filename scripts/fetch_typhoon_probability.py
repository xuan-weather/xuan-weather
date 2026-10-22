#!/usr/bin/env python3
"""Conservative OCR for CWA's official 120-hour typhoon probability map."""
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
CITY_ALIASES = {
    "台北市": "臺北市", "台中市": "臺中市", "台南市": "臺南市",
    "台東縣": "臺東縣", "臺灣": ""
}
PERCENT_RE = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*[%％]")

def norm(s):
    return (s or "").replace("台", "臺").replace("％", "%").replace(" ", "").strip()

def run_ocr(image, lang, psm, whitelist=None):
    config = f"--psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=Output.DICT)
    out = []
    for i, raw in enumerate(data["text"]):
        token = (raw or "").strip()
        if not token:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if conf < 5:
            continue
        out.append({
            "text": token, "x": int(data["left"][i]), "y": int(data["top"][i]),
            "w": int(data["width"][i]), "h": int(data["height"][i]),
            "cx": int(data["left"][i]) + int(data["width"][i]) / 2,
            "cy": int(data["top"][i]) + int(data["height"][i]) / 2,
            "conf": conf, "lang": lang, "psm": psm
        })
    return out

def main():
    import urllib3
    # Temporary workaround for the certificate-chain issue observed in GitHub Actions.
    # Scope is limited to this fixed official CWA image URL.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    response = requests.get(
        IMAGE_URL, timeout=(15, 45),
        headers={"User-Agent": "XuanWeather/1.0 official product sync"},
        verify=False
    )
    response.raise_for_status()
    if not response.headers.get("content-type", "").lower().startswith("image/"):
        raise RuntimeError("氣象署回傳內容不是圖片，停止更新以免覆蓋有效資料。")

    original = Image.open(BytesIO(response.content)).convert("RGB")
    scale = 4
    enlarged = original.resize(
        (original.width * scale, original.height * scale), Image.Resampling.LANCZOS
    )
    gray = ImageOps.grayscale(enlarged)
    enhanced = ImageEnhance.Contrast(gray).enhance(2.2)
    enhanced = enhanced.filter(ImageFilter.SHARPEN)
    # A second binarized version helps faint labels while the grayscale version
    # generally works better for anti-aliased text.
    binary = enhanced.point(lambda px: 255 if px > 165 else 0)

    city_tokens = []
    percent_tokens = []
    for img in (enhanced, binary):
        for lang in ("chi_tra", "chi_tra+eng"):
            for psm in (11, 6, 12):
                for token in run_ocr(img, lang, psm):
                    token["x"] /= scale
                    token["y"] /= scale
                    token["w"] /= scale
                    token["h"] /= scale
                    token["cx"] /= scale
                    token["cy"] /= scale
                    city_tokens.append(token)
        for psm in (11, 6, 7, 8, 13):
            for token in run_ocr(img, "eng", psm, "0123456789%"):
                token["x"] /= scale
                token["y"] /= scale
                token["w"] /= scale
                token["h"] /= scale
                token["cx"] /= scale
                token["cy"] /= scale
                percent_tokens.append(token)

    # Join OCR fragments on a row to recover split Chinese city labels.
    rows = []
    for token in sorted(city_tokens, key=lambda t: (t["cy"], t["x"])):
        row = next((r for r in rows if abs(r["cy"] - token["cy"]) <= max(8, token["h"])), None)
        if row is None:
            row = {"cy": token["cy"], "tokens": []}
            rows.append(row)
        row["tokens"].append(token)
        row["cy"] = sum(t["cy"] for t in row["tokens"]) / len(row["tokens"])

    city_boxes = {}
    preview = []
    for row in rows:
        row["tokens"].sort(key=lambda t: t["x"])
        preview.append("".join(t["text"] for t in row["tokens"]))
        for start in range(len(row["tokens"])):
            for end in range(start, min(len(row["tokens"]), start + 5)):
                group = row["tokens"][start:end + 1]
                joined = norm("".join(t["text"] for t in group))
                for city in CITIES:
                    aliases = {city, city.replace("臺", "台")}
                    if any(alias and alias in joined for alias in aliases):
                        left = min(t["x"] for t in group)
                        top = min(t["y"] for t in group)
                        right = max(t["x"] + t["w"] for t in group)
                        bottom = max(t["y"] + t["h"] for t in group)
                        box = {
                            "x": left, "y": top, "w": right-left, "h": bottom-top,
                            "cx": (left+right)/2, "cy": (top+bottom)/2,
                            "conf": min(t["conf"] for t in group)
                        }
                        current = city_boxes.get(city)
                        if current is None or box["conf"] > current["conf"]:
                            city_boxes[city] = box

    # Percentages must include a percent sign; bare numbers are intentionally ignored.
    numeric = []
    for token in percent_tokens:
        for m in PERCENT_RE.finditer(token["text"]):
            numeric.append({"percent": int(m.group(1)), **token})
    # Deduplicate OCR detections of the same printed percentage across passes.
    deduped = []
    for p in sorted(numeric, key=lambda t: t["conf"], reverse=True):
        if not any(
            abs(p["cx"]-q["cx"]) < 8 and abs(p["cy"]-q["cy"]) < 8
            and p["percent"] == q["percent"] for q in deduped
        ):
            deduped.append(p)
    numeric = deduped

    # One-to-one mutual-nearest matching: each printed percentage can be assigned
    # to at most one city, preventing one number from populating several cards.
    city_nearest = {}
    percent_nearest = {}
    for city, box in city_boxes.items():
        candidates = []
        for p in numeric:
            dx, dy = abs(p["cx"]-box["cx"]), abs(p["cy"]-box["cy"])
            if dx <= 90 and dy <= 45:
                # Percentage is usually just below or to the side of the city label.
                dist = (dx*dx + (dy*1.6)**2) ** 0.5
                candidates.append((dist, p))
        if candidates:
            city_nearest[city] = min(candidates, key=lambda x: x[0])[1]
    for p in numeric:
        candidates = []
        for city, box in city_boxes.items():
            dx, dy = abs(p["cx"]-box["cx"]), abs(p["cy"]-box["cy"])
            if dx <= 90 and dy <= 45:
                dist = (dx*dx + (dy*1.6)**2) ** 0.5
                candidates.append((dist, city))
        if candidates:
            percent_nearest[id(p)] = min(candidates, key=lambda x: x[0])[1]

    values = {}
    for city, p in city_nearest.items():
        if percent_nearest.get(id(p)) != city:
            continue
        # Reject weak matches and suspiciously distant pairs.
        box = city_boxes[city]
        dx, dy = abs(p["cx"]-box["cx"]), abs(p["cy"]-box["cy"])
        if min(p["conf"], box["conf"]) < 15 or dx > 75 or dy > 38:
            continue
        values[city] = {
            "percent": p["percent"],
            "ocr_confidence": round(min(p["conf"], box["conf"]), 1),
            "method": "multi-pass-mutual-nearest"
        }

    print("OCR text preview:", " | ".join(preview[:80]))
    print(f"Official map fetched: {original.width}x{original.height}; OCR matched {len(values)}/{len(CITIES)} locations.")
    print("Recognized values:", json.dumps(values, ensure_ascii=False))

    if len(values) < 15:
        raise RuntimeError(
            f"OCR 只辨識並配對到 {len(values)}/{len(CITIES)} 個地點，低於安全門檻 15；"
            "保留上一版 JSON，不發布不完整或可能錯配的資料。請查看上方 OCR text preview。"
        )

    payload = {
        "source": IMAGE_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_last_modified": response.headers.get("Last-Modified"),
        "image_dimensions": {"width": original.width, "height": original.height},
        "values": values,
        "recognized_count": len(values),
        "note": "OCR-derived from the official CWA map; values are published only after conservative one-to-one pairing."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp_out = OUT.with_suffix(".json.tmp")
    temp_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_out.replace(OUT)

if __name__ == "__main__":
    main()

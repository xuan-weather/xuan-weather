#!/usr/bin/env python3
"""Download CWA's official 120-hour typhoon wind-probability map and OCR city values.
Only emits values that can be tied to a city label and a percentage on the current official image.
"""
import json, os, re, sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageOps, ImageEnhance
import pytesseract
from pytesseract import Output

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'typhoon-probability.json'
IMAGE_URL = 'https://www.cwa.gov.tw/Data/typhoon/WSP_CH.png'
CITIES = [
    '基隆市','臺北市','新北市','桃園市','新竹市','新竹縣','苗栗縣','臺中市',
    '彰化縣','南投縣','雲林縣','嘉義市','嘉義縣','臺南市','高雄市','屏東縣',
    '宜蘭縣','花蓮縣','臺東縣','澎湖縣','金門縣','連江縣','綠島','蘭嶼','恆春'
]
ALIASES = {'台北市':'臺北市','台中市':'臺中市','台南市':'臺南市','台東縣':'臺東縣'}

def norm(s):
    s = re.sub(r'\s+', '', str(s or '')).replace('台','臺')
    return ALIASES.get(s, s)

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; XuanWeather-TyphoonSync/1.1)',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Accept': 'image/avif,image/webp,image/apng,image/png,image/*,*/*;q=0.8',
    }
    params = {'_': int(datetime.now().timestamp())}

    # Try normal TLS certificate validation first. If the official CWA image
    # endpoint presents a broken certificate chain on the GitHub runner, retry
    # only this fixed public CWA image URL with verification disabled. The
    # response is still checked for official host, HTTP status and image type.
    try:
        r = requests.get(IMAGE_URL, timeout=(10, 35), headers=headers, params=params,
                         allow_redirects=True)
        r.raise_for_status()
    except requests.exceptions.SSLError as ssl_error:
        print(f'警告：氣象署圖片 HTTPS 憑證驗證失敗，啟用限定網址備援：{ssl_error}', file=sys.stderr)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(IMAGE_URL, timeout=(10, 35), headers=headers, params=params,
                         allow_redirects=True, verify=False)
        r.raise_for_status()

    final_host = (requests.utils.urlparse(r.url).hostname or '').lower()
    if final_host not in ('www.cwa.gov.tw', 'cwa.gov.tw'):
        raise RuntimeError(f'下載被重新導向至非氣象署網域，停止處理：{r.url}')
    if not r.headers.get('content-type','').lower().startswith('image/'):
        raise RuntimeError(f'官方圖回傳不是圖片：{r.headers.get("content-type")}')
    image = Image.open(BytesIO(r.content)).convert('RGB')
    if image.width < 200 or image.height < 200:
        raise RuntimeError(f'官方圖尺寸異常：{image.size}')
    # OCR the actual official graphic. CWA map labels often merge city names
    # and percentages into one OCR token (e.g. "新北市基隆市100%"), so parse
    # both individual words and reconstructed text lines rather than requiring
    # exact city-name and percentage tokens.
    scale = 3
    original_size = image.size
    enlarged = image.resize((image.width * scale, image.height * scale))
    gray = ImageOps.grayscale(enlarged)
    gray = ImageEnhance.Contrast(gray).enhance(1.8)
    gray = ImageEnhance.Sharpness(gray).enhance(2)

    # Keep the image OCR-only; do not infer values from colors or fill missing values.
    city_patterns = sorted(CITIES, key=len, reverse=True)
    city_regex = re.compile("|".join(re.escape(c) for c in city_patterns))
    percent_regex = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*%?")
    detections = {}

    def add_detection(city, percent, x, y, confidence):
        if city not in CITIES or not (0 <= percent <= 100) or confidence < 10:
            return
        # Keep the strongest reading for a city, but prefer confidence over position.
        old = detections.get(city)
        score = confidence
        if old is None or score > old["score"]:
            detections[city] = {
                "percent": int(percent), "score": score,
                "x": float(x), "y": float(y)
            }

    # Several page segmentation modes help when the official map layout changes.
    for psm in (11, 6, 12):
        data = pytesseract.image_to_data(
            gray, lang="chi_tra+eng",
            config=f"--psm {psm}",
            output_type=Output.DICT
        )
        lines = {}
        n = len(data.get("text", []))
        for i in range(n):
            raw = (data["text"][i] or "").strip()
            if not raw:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            x0 = float(data["left"][i]) / scale
            y0 = float(data["top"][i]) / scale
            w0 = float(data["width"][i]) / scale
            h0 = float(data["height"][i]) / scale
            key = (data.get("page_num", [1]*n)[i],
                   data.get("block_num", [0]*n)[i],
                   data.get("par_num", [0]*n)[i],
                   data.get("line_num", [0]*n)[i])
            lines.setdefault(key, []).append({
                "text": re.sub(r"\s+", "", raw),
                "x": x0 + w0/2, "y": y0 + h0/2,
                "left": x0, "right": x0+w0,
                "conf": conf
            })

        for tokens in lines.values():
            tokens.sort(key=lambda t: t["left"])
            line_text = "".join(t["text"] for t in tokens)
            if not line_text:
                continue

            # Locate city labels and percentage strings in the reconstructed line.
            city_hits = []
            for match in city_regex.finditer(line_text):
                city_hits.append((norm(match.group(0)), match.start(), match.end()))
            percent_hits = []
            for match in percent_regex.finditer(line_text):
                # Reject obvious fragments embedded in longer digit sequences.
                try:
                    p = int(match.group(1))
                except ValueError:
                    continue
                percent_hits.append((p, match.start(), match.end()))

            if not city_hits or not percent_hits:
                continue

            # Approximate character positions along the line using token geometry.
            # This lets fused tokens still associate a nearby percentage with its label.
            char_map = []
            for token in tokens:
                txt = token["text"]
                for j in range(len(txt)):
                    char_map.append((token["x"], token["y"], token["conf"]))
            if len(char_map) < len(line_text):
                char_map.extend([(tokens[-1]["x"], tokens[-1]["y"], tokens[-1]["conf"])] *
                                (len(line_text)-len(char_map)))

            for p, p_start, p_end in percent_hits:
                p_idx = (p_start + p_end - 1) // 2
                px, py, pconf = char_map[min(p_idx, len(char_map)-1)]
                # Prefer a city label on the same line closest to this percentage.
                city, cstart, cend = min(
                    city_hits,
                    key=lambda hit: abs(((hit[1]+hit[2]-1)//2) - p_idx)
                )
                c_idx = (cstart + cend - 1) // 2
                cx, cy, cconf = char_map[min(c_idx, len(char_map)-1)]
                # If a line contains several cities, only pair when the textual
                # distance is local; otherwise it is too ambiguous to trust.
                char_distance = abs(p_idx - c_idx)
                if len(city_hits) > 1 and char_distance > 18:
                    continue
                if abs(py-cy) > 28:
                    continue
                add_detection(city, p, px, py, min(pconf, cconf))

    values = {city: {"percent": item["percent"]}
              for city, item in detections.items()}
    print(f'OCR 診斷：辨識到 {len(values)} 個地點：' + ', '.join(f'{k}={v["percent"]}%' for k,v in values.items()))
    if len(values) < 5:
        # Preserve prior file only as a last-known cache, but mark the attempt as failed separately;
        # front end will not mistake this run for a fresh successful result.
        raise RuntimeError(f'官方圖 OCR 僅辨識 {len(values)}/{len(CITIES)} 個地點，低於安全門檻；不發布不完整新資料。請檢查 OCR 語言包、官方圖版面及下方 OCR 診斷紀錄。')
    payload={
        'source':'中央氣象署官方 120 小時颱風暴風侵襲機率圖',
        'source_url':IMAGE_URL,
        'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'),
        'source_last_modified':r.headers.get('Last-Modified'),
        'image_width':Image.open(BytesIO(r.content)).width,
        'image_height':Image.open(BytesIO(r.content)).height,
        'values':values,
        'ocr_note':'百分比由官方圖 OCR 辨識；未能可靠辨識的地點不輸出。'
    }
    tmp=OUT.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(OUT)
    print(f'已更新 {OUT}: {len(values)}/{len(CITIES)} 個地點')
    print(json.dumps(values,ensure_ascii=False))

if __name__=='__main__':
    try: main()
    except Exception as e:
        print(f'ERROR: {e}',file=sys.stderr)
        sys.exit(1)

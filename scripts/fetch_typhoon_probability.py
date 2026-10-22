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
    r = requests.get(IMAGE_URL, timeout=30, headers={'User-Agent':'XuanWeather typhoon-probability sync/1.0','Cache-Control':'no-cache'}, params={'_':int(datetime.now().timestamp())})
    r.raise_for_status()
    if not r.headers.get('content-type','').lower().startswith('image/'):
        raise RuntimeError(f'官方圖回傳不是圖片：{r.headers.get("content-type")}')
    image = Image.open(BytesIO(r.content)).convert('RGB')
    if image.width < 200 or image.height < 200:
        raise RuntimeError(f'官方圖尺寸異常：{image.size}')
    # OCR the actual official graphic; no hand-entered or fabricated percentages.
    scale = 3
    image = image.resize((image.width*scale, image.height*scale))
    image = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.6)
    image = ImageEnhance.Sharpness(image).enhance(2)
    data = pytesseract.image_to_data(image, lang='chi_tra+eng', config='--psm 11', output_type=Output.DICT)
    words=[]
    for i, raw in enumerate(data['text']):
        text = re.sub(r'\s+','',raw or '')
        if not text: continue
        x=(data['left'][i]+data['width'][i]/2)/scale
        y=(data['top'][i]+data['height'][i]/2)/scale
        conf=float(data['conf'][i]) if str(data['conf'][i]).replace('.','',1).lstrip('-').isdigit() else -1
        words.append({'text':text,'x':x,'y':y,'conf':conf})
    city_boxes=[]
    for w in words:
        label=norm(w['text'])
        # OCR can split city labels across adjacent words; combine nearby words on same line.
        if label in CITIES:
            city_boxes.append((label,w['x'],w['y'],w['conf']))
    # Try joining nearby OCR words to handle labels separated by OCR whitespace.
    for a in words:
        for b in words:
            if a is b or abs(a['y']-b['y']) > 12 or not (0 < b['x']-a['x'] < 55):
                continue
            joined=norm(a['text']+b['text'])
            if joined in CITIES:
                city_boxes.append((joined,(a['x']+b['x'])/2,(a['y']+b['y'])/2,min(a['conf'],b['conf'])))
    # Keep the highest-confidence location for each city label.
    city_pos={}
    for city,x,y,conf in city_boxes:
        if city not in city_pos or conf>city_pos[city][2]: city_pos[city]=(x,y,conf)
    perc=[]
    for w in words:
        m=re.fullmatch(r'(100|[1-9]?\d)\s*%?',w['text'])
        if m:
            p=int(m.group(1))
            if 0 <= p <= 100 and w['conf'] >= 15:
                perc.append((p,w['x'],w['y'],w['conf']))
    values={}
    # Each percentage is assigned only to its nearest recognized city label, within a conservative map distance.
    for p,x,y,conf in perc:
        nearest=None; dist=1e9
        for city,(cx,cy,cconf) in city_pos.items():
            d=((x-cx)**2+(y-cy)**2)**0.5
            if d<dist: nearest,dist=city,d
        if nearest and dist <= 78:
            old=values.get(nearest)
            score=conf-dist*0.1
            if old is None or score>old['_score']:
                values[nearest]={'percent':p,'_score':score}
    values={k:{'percent':v['percent']} for k,v in values.items()}
    if len(values) < 5:
        # Preserve prior file only as a last-known cache, but mark the attempt as failed separately;
        # front end will not mistake this run for a fresh successful result.
        raise RuntimeError(f'官方圖 OCR 成功辨識 {len(values)} 個地點，低於安全門檻；不發布不完整新資料。')
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

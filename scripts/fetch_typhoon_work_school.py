#!/usr/bin/env python3
"""Synchronize DGPA work/class suspension notices into the site's JSON schema.

This deliberately consumes the official DGPA public notice page and preserves
its wording in summary; it does not invent county or township announcements.
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from html.parser import HTMLParser

URL = 'https://www.dgpa.gov.tw/typh/daily/nds.html?uid=31'
OUT = Path('data/typhoon-work-school.json')
COUNTIES = ['臺北市','新北市','桃園市','臺中市','臺南市','高雄市','基隆市','新竹市','嘉義市','新竹縣','苗栗縣','彰化縣','南投縣','雲林縣','嘉義縣','屏東縣','宜蘭縣','花蓮縣','臺東縣','澎湖縣','金門縣','連江縣']
ALIASES = {'台北市':'臺北市','台中市':'臺中市','台南市':'臺南市','台東縣':'臺東縣','台':'臺'}

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.row=None; self.cell=None; self.in_cell=False
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]; self.in_cell=True
    def handle_data(self, data):
        if self.in_cell and self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td','th') and self.in_cell and self.row is not None:
            self.row.append(' '.join(' '.join(self.cell).split())); self.cell=None; self.in_cell=False
        elif tag == 'tr' and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row=None

def norm(s):
    for a,b in ALIASES.items(): s=s.replace(a,b)
    return re.sub(r'\s+', ' ', s).strip()

def parse_status(text):
    t=norm(text)
    work='unknown'; school='unknown'
    if any(x in t for x in ('停止上班','停止辦公','停班')): work='stop'
    elif any(x in t for x in ('照常上班','正常上班','上班上課照常')): work='normal'
    if any(x in t for x in ('停止上課','停課')): school='stop'
    elif any(x in t for x in ('照常上課','正常上課','上班上課照常')): school='normal'
    if '部分' in t or any(x in t for x in ('區停止','鄉停止','鎮停止','市停止','里停止')):
        if work == 'stop': work='partial'
        if school == 'stop': school='partial'
    return work, school

def main():
    req=Request(URL, headers={'User-Agent':'XuanWeather/1.0 (official public notice sync)'})
    with urlopen(req, timeout=30) as resp:
        raw=resp.read()
        charset=resp.headers.get_content_charset() or 'utf-8'
        html=raw.decode(charset, errors='replace')
    text=re.sub(r'<[^>]+>', ' ', html)
    text=norm(text.replace('&nbsp;',' ').replace('&amp;','&'))
    if '天然災害停止上班及上課情形' not in text and '停止上班及上課' not in text:
        raise RuntimeError('官方頁面內容格式不符，為避免發布錯誤資料，停止更新 JSON。')

    parser=TableParser(); parser.feed(html)
    rows=[]
    for row in parser.rows:
        cells=[norm(c) for c in row if norm(c)]
        if cells: rows.append(cells)
    # Collect only rows that contain an explicit county/city and an announcement.
    found={}
    for cells in rows:
        rowtext=norm('；'.join(cells))
        matched=[c for c in COUNTIES if c in rowtext]
        if not matched: continue
        # Avoid interpreting the table's header as a notice.
        if '縣市名稱' in rowtext or '是否停止上班上課情形' in rowtext: continue
        work, school=parse_status(rowtext)
        if work == 'unknown' and school == 'unknown': continue
        for county in matched:
            found[county]={'county':county,'work':work,'school':school,'date':'—','publishedAt':'','summary':rowtext,'sourceUrl':URL}

    no_notice=any(x in text for x in ('無停班停課訊息','無停止上班及上課訊息','目前無停班停課訊息'))
    if not found and not no_notice:
        raise RuntimeError('頁面沒有可辨識的公告列，也沒有明確的「無停班停課訊息」文字；保留舊 JSON，避免誤報。')

    # The DGPA page's explicit no-notice message means there are no listed suspensions;
    # populate normal status only in that specific verified case.
    announcements=[]
    for county in COUNTIES:
        item=found.get(county)
        if item:
            announcements.append(item)
        elif no_notice:
            announcements.append({'county':county,'work':'normal','school':'normal','date':datetime.now().astimezone().strftime('%Y/%m/%d'),'publishedAt':'','summary':'人事行政總處官方頁面目前顯示「無停班停課訊息」；如地方政府另有最新公告，請以地方政府公告為準。','sourceUrl':URL})
        else:
            announcements.append({'county':county,'work':'unknown','school':'unknown','date':'—','publishedAt':'','summary':'官方頁面未找到此縣市的可辨識公告項目，請查看原始公告。','sourceUrl':URL})

    payload={'sourceName':'行政院人事行政總處','sourceUrl':URL,'updatedAt':datetime.now(timezone.utc).isoformat(),'announcements':announcements}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"Wrote {len(announcements)} county records to {OUT}; explicit_no_notice={no_notice}; parsed_announcements={len(found)}")

if __name__ == '__main__':
    try: main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr); sys.exit(1)

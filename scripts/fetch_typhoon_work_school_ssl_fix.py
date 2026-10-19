import json, re, sys, ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from html.parser import HTMLParser

URL = "https://www.dgpa.gov.tw/typh/daily/nds.html?uid=31"
NAME = "行政院人事行政總處－天然災害停止上班及上課情形"
OUT = Path("data/typhoon-work-school.json")
TZ = timezone(timedelta(hours=8))
COUNTIES = ["臺北市","新北市","桃園市","臺中市","臺南市","高雄市","基隆市","新竹市","嘉義市","新竹縣","苗栗縣","彰化縣","南投縣","雲林縣","嘉義縣","屏東縣","宜蘭縣","花蓮縣","臺東縣","澎湖縣","金門縣","連江縣"]

class Extract(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ("script","style"): self.skip += 1
        if tag in ("tr","td","th","p","br","li","div"): self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script","style") and self.skip: self.skip -= 1
        if tag in ("tr","td","th","p","li","div"): self.parts.append("\n")
    def handle_data(self, data):
        if not self.skip:
            value=re.sub(r"\s+"," ",data).strip()
            if value: self.parts.append(value)

def classify(text):
    t=re.sub(r"\s+","",text)
    sw=bool(re.search(r"停止上班|停止辦公|不上班",t))
    ss=bool(re.search(r"停止上課|不上課|停課",t))
    nw=bool(re.search(r"正常上班",t)); ns=bool(re.search(r"正常上課",t))
    if sw and ss: return "stop"
    if (sw or ss) and not (nw or ns): return "partial"
    if nw and ns and not sw and not ss: return "normal"
    return "unknown"

def main():
    req=Request(URL,headers={"User-Agent":"XuanWeather/1.0 official notice reader"})
    # 暫時性相容措施：官方公告頁目前回傳的憑證鏈觸發 Missing Subject Key Identifier。
    # 僅用於讀取公開公告頁；TLS 憑證驗證會被略過，存在中間人風險。
    # 若來源網站修復憑證，請移除此 context，恢復標準憑證驗證。
    insecure_context = ssl._create_unverified_context()
    with urlopen(req, timeout=25, context=insecure_context) as r:
        raw=r.read(); charset=r.headers.get_content_charset() or "utf-8"
    parser=Extract(); parser.feed(raw.decode(charset,errors="replace"))
    text="\n".join(x.strip() for x in parser.parts if x.strip())
    if "天然災害停止上班及上課情形" not in text or "更新時間" not in text:
        raise RuntimeError("來源頁標題或更新時間無法辨識；停止覆寫 JSON。")
    m=re.search(r"更新時間\s*[:：]?\s*(20\d{2}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)",text)
    updated=m.group(1) if m else ""
    notice=text.split("若欲進入本總處網站首頁版面")[0][:12000]
    items=[]
    for county in COUNTIES:
        status="unknown"; summary="尚未取得可明確判讀的公告"
        for hit in re.finditer(re.escape(county),notice):
            context=notice[hit.start():hit.start()+350]
            if re.search(r"停止上班|停止上課|停止辦公|正常上班|正常上課|不上班|不上課|停課",context):
                status=classify(context); summary=re.sub(r"\s+"," ",context[:160]).strip()
                if status!="unknown": break
        if status!="unknown":
            items.append({"county":county,"work":status,"school":status,"date":datetime.now(TZ).strftime("%Y-%m-%d"),"publishedAt":updated,"summary":summary,"sourceUrl":URL})
    payload={"updatedAt":datetime.now(TZ).isoformat(timespec="seconds"),"sourceUpdatedAt":updated,"sourceName":NAME,"sourceUrl":URL,"sourceStatus":"ok","note":"只辨識來源頁明確文字；未能判讀者顯示尚未取得公告。","announcements":items}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"已產生 JSON；明確判讀公告 {len(items)} 筆；來源更新時間：{updated or '未辨識'}")
if __name__=="__main__":
    try: main()
    except Exception as e: print(f"ERROR: {e}",file=sys.stderr); raise

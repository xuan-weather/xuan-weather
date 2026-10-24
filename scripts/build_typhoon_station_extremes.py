#!/usr/bin/env python3
"""Backfill per-station extremes from CWA snapshots retained in Git history."""
import json, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path("."); ALERTS=ROOT/"data/national-alerts.json"; WEATHER=ROOT/"data/cwa-O-A0003-001.json"; OUTPUT=ROOT/"data/typhoon-station-extremes.json"
TZ8=timezone(timedelta(hours=8)); NOW=datetime.now(TZ8)

def dt(value):
    if not value: return None
    s=str(value).strip()
    m=re.match(r"^(\\d{4})/(\\d{1,2})/(\\d{1,2})\\s*(上午|下午)?\\s*(\\d{1,2}):(\\d{2})(?::(\\d{2}))?$",s)
    if m:
        y,mo,d,ap,h,mi,se=m.groups(); h=int(h)
        if ap=="下午" and h<12:h+=12
        elif ap=="上午" and h==12:h=0
        return datetime(int(y),int(mo),int(d),h,int(mi),int(se or 0),tzinfo=TZ8)
    try:
        x=datetime.fromisoformat(s.replace("Z","+00:00"))
        return x.replace(tzinfo=TZ8) if x.tzinfo is None else x.astimezone(TZ8)
    except ValueError:return None

def iso(x): return x.astimezone(TZ8).isoformat(timespec="seconds") if x else None
def run(args): return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=False).stdout
def commits(path,since=None):
    args=["git","log","--format=%H","--reverse"]
    if since: args += ["--since="+since.isoformat()]
    args += ["--",path]
    return run(args).decode("ascii","ignore").split()
def load_bytes(b):
    try:return json.loads(b.decode("utf-8-sig"))
    except Exception:return None
def show(sha,path):
    b=run(["git","show",sha+":"+path]);return load_bytes(b) if b else None
def alerts(payload):
    out=[]
    for a in (payload or {}).get("alerts",[]) if isinstance(payload,dict) else []:
        title=str(a.get("title") or ""); author=str(a.get("author") or "")
        if "颱風" not in title and "typhoon" not in title.lower():continue
        if author and "氣象署" not in author and "CWA" not in author.upper():continue
        if a.get("isDrill") is True or a.get("status") not in (None,"","Actual"):continue
        effective=dt(a.get("effective")) or dt(a.get("publishedAt")) or dt(a.get("updated"))
        if effective:out.append({"id":str(a.get("id") or ""),"effective":effective,"expires":dt(a.get("expires")),"msgType":str(a.get("msgType") or "")})
    return out
def warning_start():
    events={}
    for sha in commits(str(ALERTS),NOW-timedelta(days=14)):
        for a in alerts(show(sha,str(ALERTS))):
            events[(a["id"],iso(a["effective"]))]=a
    current=load_bytes(ALERTS.read_bytes()) if ALERTS.exists() else {}
    current_events=alerts(current)
    for a in current_events:events[(a["id"],iso(a["effective"]))]=a
    items=sorted(events.values(),key=lambda x:x["effective"])
    if not items:return None,None,False,False
    latest=items[-1]; chain=[latest]
    for item in reversed(items[:-1]):
        gap=(chain[0]["effective"]-item["effective"]).total_seconds()/3600
        if gap>12:break
        chain.insert(0,item)
    start=chain[0]["effective"]
    latest_now=sorted(current_events,key=lambda x:x["effective"])[-1] if current_events else latest
    active=bool(latest_now["expires"] and latest_now["expires"]>=NOW)
    explicit=any(x["msgType"].lower() in ("issue","alert") for x in chain if x["effective"]==start)
    return start,latest_now["id"],active,explicit
def num(v):
    try:
        x=float(v);return x if 0<=x<10000 else None
    except (TypeError,ValueError):return None
def rows(payload):
    x=((payload or {}).get("records") or {}).get("Station") or []
    return x if isinstance(x,list) else []
def coords(st):
    cs=((st.get("GeoInfo") or {}).get("Coordinates") or [])
    for c in cs:
        if str(c.get("CoordinateName","")).upper()=="WGS84":
            a,b=num(c.get("StationLatitude")),num(c.get("StationLongitude"))
            if a is not None and b is not None:return a,b
    if cs:
        a,b=num(cs[0].get("StationLatitude")),num(cs[0].get("StationLongitude"))
        if a is not None and b is not None:return a,b
    return None,None
def metric_update(entry,key,value,when,fallback,start):
    value=num(value); occurrence=dt(when) or fallback
    if value is None or occurrence is None or occurrence<start:return
    old=entry.get(key)
    if old is None or value>old["value"]:entry[key]={"value":value,"time":iso(occurrence)}
def merge(stations,payload,start):
    newest=None
    for raw in rows(payload):
        sid=str(raw.get("StationId") or "").strip()
        if not sid:continue
        obs=dt((raw.get("ObsTime") or {}).get("DateTime"))
        if obs is None or obs<start:continue
        newest=max(newest,obs) if newest else obs
        lat,lon=coords(raw)
        if lat is None or lon is None:continue
        geo=raw.get("GeoInfo") or {}; el=(raw.get("WeatherElement") or {})
        e=stations.setdefault(sid,{"id":sid,"name":raw.get("StationName") or sid,"county":geo.get("CountyName") or "","town":geo.get("TownName") or "","lat":lat,"lon":lon,"gust":None,"mean":None,"temp":None,"wind":None,"lastObservedAt":None,"observations":0})
        e.update({"name":raw.get("StationName") or e["name"],"county":geo.get("CountyName") or e["county"],"town":geo.get("TownName") or e["town"],"lat":lat,"lon":lon})
        gi=el.get("GustInfo") or {}; mo=el.get("Max10MinAverage") or {}
        dh=(((el.get("DailyExtreme") or {}).get("DailyHigh") or {}).get("TemperatureInfo") or {})
        metric_update(e,"gust",gi.get("PeakGustSpeed"),((gi.get("Occurred_at") or {}).get("DateTime")),obs,start)
        metric_update(e,"mean",mo.get("WindSpeed"),((mo.get("Occurred_at") or {}).get("DateTime")),obs,start)
        metric_update(e,"temp",el.get("AirTemperature"),(raw.get("ObsTime") or {}).get("DateTime"),obs,start)
        metric_update(e,"temp",dh.get("AirTemperature"),((dh.get("Occurred_at") or {}).get("DateTime")),obs,start)
        metric_update(e,"wind",el.get("WindSpeed"),(raw.get("ObsTime") or {}).get("DateTime"),obs,start)
        e["lastObservedAt"]=iso(obs);e["observations"]+=1
    return newest

def main():
    if not ALERTS.exists() or not WEATHER.exists():
        print("Required national-alerts or CWA station data missing.",file=sys.stderr);return 2
    start,warn_id,active,explicit=warning_start()
    try:old=json.loads(OUTPUT.read_text(encoding="utf-8"))
    except Exception:old={}
    old_start=dt(old.get("warningStartAt"))
    same=bool(start and old_start and abs((old_start-start).total_seconds())<12*3600 and old.get("stations"))
    if same:
        stations={str(s["id"]):s for s in old.get("stations",[]) if s.get("id")}
        last=dt(old.get("lastSampleTime")); count=int(old.get("snapshotCount") or 0)
        coverage=dt(old.get("coverageStartAt")) or start; backfill=bool(old.get("backfillComplete"))
    else:
        stations={};last=None;count=0;coverage=start or NOW;backfill=False
    rel=str(WEATHER)
    if start and not backfill:
        for sha in commits(rel,start-timedelta(minutes=15)):
            p=show(sha,rel); rr=rows(p)
            if not rr:continue
            times=[dt((x.get("ObsTime") or {}).get("DateTime")) for x in rr]
            sample=max([x for x in times if x] or [None])
            if not sample or sample<start or (last and sample<=last):continue
            merge(stations,p,start);last=sample;count+=1
        backfill=True
    latest=load_bytes(WEATHER.read_bytes())
    rr=rows(latest);times=[dt((x.get("ObsTime") or {}).get("DateTime")) for x in rr]
    sample=max([x for x in times if x] or [None])
    if sample and start and sample>=start and (last is None or sample>last):
        merge(stations,latest,start);last=sample;count+=1
    elif sample and not start and (last is None or sample>last):
        merge(stations,latest,sample);last=sample;count+=1;coverage=coverage or sample
    output={"ok":True,"source":"中央氣象署 O-A0003-001；使用 Xuan Weather Git 歷史快照回溯","warningStartAt":iso(start),"warningId":warn_id,"warningActive":active,"warningStartExplicit":explicit,"coverageStartAt":iso(coverage),"lastSampleTime":iso(last),"lastUpdated":iso(NOW),"snapshotCount":count,"recordCount":len(stations),"backfillComplete":backfill,"notes":["最大陣風與最大 10 分鐘平均風速使用官方欄位及發生時間。","最高氣溫使用警報起始後的觀測氣溫與日最高氣溫紀錄。","回溯範圍限於 Git 儲存庫保留的官方觀測快照；未保存的時間點無法補造。"],"stations":sorted(stations.values(),key=lambda x:(x.get("county",""),x.get("name","")))}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True);tmp=OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(output,ensure_ascii=False,separators=(",",":"))+"\\n",encoding="utf-8");tmp.replace(OUTPUT)
    print("Saved",len(stations),"stations;",count,"snapshots; warning start",iso(start),"last sample",iso(last))
    return 0
if __name__=="__main__":sys.exit(main())

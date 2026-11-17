#!/usr/bin/env python3
"""Fetch CWA O-A0059-001 historyAPI for the latest 24h and render georeferenced PNG frames."""
from __future__ import annotations
import os, json, re, sys, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
import requests
import xml.etree.ElementTree as ET
from PIL import Image

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "radar-history"
MANIFEST = OUT / "manifest.json"
DATA_ID = "O-A0059-001"
API_KEY = os.environ.get("CWA_API_KEY", "").strip()
BASE = "https://opendata.cwa.gov.tw"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "XuanWeather-RadarHistory/1.0"})

# CWA QPESUMS reflectivity palette, ordered by the published dBZ bins.
PALETTE = [
    (242,242,242,0), (255,255,255,0),
    (7,253,253,255), (6,149,253,255), (2,3,249,255),
    (0,255,0,255), (0,200,0,255), (1,149,0,255),
    (254,253,2,255), (254,200,1,255), (253,122,0,255),
    (251,1,0,255), (199,1,0,255), (149,1,0,255),
    (250,3,250,255), (152,0,246,255)
]
THRESHOLDS = [-1000, -900, 0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70]

def get_json(url, **kwargs):
    r = SESSION.get(url, timeout=90, **kwargs)
    r.raise_for_status()
    return r.json()

def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk_dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk_dicts(v)

def parse_time(value):
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ)
        return d.astimezone(TZ)
    except ValueError:
        return None

def get_metadata_records(payload):
    """
    CWA historyAPI JSON shape (confirmed from actual Actions response):
    resources.resource.data.time = [
      {"DateTime": "...", "UpdateTime": "...", "ProductURL": "..."}
    ]
    """
    resource = (payload.get("resources") or {}).get("resource") or {}
    data = resource.get("data") or {}
    time_rows = data.get("time") or []
    if isinstance(time_rows, dict):
        time_rows = [time_rows]

    found = {}
    for row in time_rows:
        if not isinstance(row, dict):
            continue
        dt = parse_time(row.get("DateTime"))
        product_url = row.get("ProductURL")
        if not dt or not isinstance(product_url, str) or not product_url.startswith("https://"):
            continue
        found[dt.strftime("%Y%m%d%H%M")] = (dt, product_url)

    # Keep a defensive fallback for alternate CWA JSON response versions.
    if not found:
        for obj in walk_dicts(payload):
            product_url = obj.get("ProductURL") or obj.get("url") or obj.get("URL")
            dt = parse_time(obj.get("DateTime") or obj.get("datetime") or obj.get("time"))
            if dt and isinstance(product_url, str) and product_url.startswith("https://"):
                found[dt.strftime("%Y%m%d%H%M")] = (dt, product_url)

    return sorted(found.values(), key=lambda x: x[0])

def fetch_history_metadata(start, end, data_id=DATA_ID):
    url = f"{BASE}/historyapi/v1/getMetadata/{data_id}"
    params = {
        "Authorization": API_KEY,
        "format": "JSON",
        "timeFrom": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "timeTo": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "limit": 300
    }
    payload = get_json(url, params=params)
    records = get_metadata_records(payload)
    if not records:
        result = payload.get("result") or {}
        resources = payload.get("resources") or {}
        resource = resources.get("resource") or {}
        data = resource.get("data") or {}
        keys = list(data.keys()) if isinstance(data, dict) else []
        raise RuntimeError(
            "歷史 API 有回應，但 resources.resource.data.time 中沒有可辨識的 "
            f"DateTime/ProductURL。success={((payload.get('dataset') or {}).get('success'))}; "
            f"resource_id={result.get('resource_id')}; data_keys={keys}"
        )
    return records

def xml_text(root, local_name):
    for el in root.iter():
        if el.tag.split("}")[-1] == local_name and el.text:
            return el.text.strip()
    return None

def xml_parameter(root, name):
    # CWA XML commonly stores parameterName and parameterValue as siblings.
    for parent in root.iter():
        children = list(parent)
        for i, child in enumerate(children):
            if child.tag.split("}")[-1] == "parameterName" and (child.text or "").strip() == name:
                for sib in children[i+1:]:
                    if sib.tag.split("}")[-1] == "parameterValue":
                        return (sib.text or "").strip()
    return None

def parse_grid(xml_bytes):
    root = ET.fromstring(xml_bytes)
    dt_text = xml_text(root, "DateTime") or xml_parameter(root, "時間")
    dt = parse_time(dt_text)
    if not dt:
        raise ValueError("XML 缺少有效 DateTime")
    content = xml_text(root, "content")
    if not content:
        raise ValueError("XML 缺少 content 格點欄位")
    vals = [float(x) for x in re.split(r"[\s,;]+", content.strip()) if x]
    nx = int(xml_parameter(root, "GridDimensionX") or xml_text(root, "GridDimensionX") or 921)
    ny = int(xml_parameter(root, "GridDimensionY") or xml_text(root, "GridDimensionY") or 881)
    if len(vals) != nx * ny:
        raise ValueError(f"格點數量不符：取得 {len(vals)}，預期 {nx}×{ny}")
    lon0 = float(xml_parameter(root, "StartPointLongitude") or xml_text(root, "StartPointLongitude") or 115.0)
    lat0 = float(xml_parameter(root, "StartPointLatitude") or xml_text(root, "StartPointLatitude") or 18.0)
    res = float(xml_parameter(root, "GridResolution") or xml_text(root, "GridResolution") or 0.0125)
    return dt, vals, nx, ny, lon0, lat0, res

def render_png(vals, nx, ny, out_path):
    # QPESUMS grid rows are south-to-north; PNG rows are north-to-south.
    rgba = bytearray(nx * ny * 4)
    for row in range(ny):
        src_row = ny - 1 - row
        for col in range(nx):
            v = vals[src_row * nx + col]
            p = (row * nx + col) * 4
            if v <= -90:
                continue
            idx = 0
            for i in range(1, len(THRESHOLDS)-1):
                if v >= THRESHOLDS[i]:
                    idx = i
                else:
                    break
            color = PALETTE[idx]
            rgba[p:p+4] = bytes(color)
    im = Image.frombytes("RGBA", (nx, ny), bytes(rgba))
    im.save(out_path, format="PNG", optimize=True)

def main():
    if not API_KEY:
        raise SystemExit("缺少 CWA_API_KEY；請在 GitHub Repository Secrets 新增中央氣象署授權碼。")
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    start = now - timedelta(hours=24)
    existing = {}
    if MANIFEST.exists():
        try:
            old = json.loads(MANIFEST.read_text(encoding="utf-8"))
            for f in old.get("frames", []):
                existing[f.get("time")] = f
        except Exception:
            pass
    records = fetch_history_metadata(start, now)

    # O-A0058-005 is the live composite PNG displayed by the website.
    # When CWA exposes its history metadata, publish the official observation
    # DateTime separately so the frontend does not infer it from S3 Last-Modified.
    live_time_file = OUT / "latest-image-time.json"
    try:
        live_records = fetch_history_metadata(start, now, data_id="O-A0058-005")
        live_dt, live_product_url = max(live_records, key=lambda item: item[0])
        live_time_file.write_text(json.dumps({
            "source": "Central Weather Administration O-A0058-005",
            "data_id": "O-A0058-005",
            "time": live_dt.isoformat(),
            "fetched_at": datetime.now(TZ).isoformat(),
            "product_url": live_product_url
        }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Official O-A0058-005 observation time: {live_dt.isoformat()}")
    except Exception as exc:
        # Do not fail the existing O-A0059-001 history pipeline if this product
        # does not support historyAPI or CWA temporarily returns an error.
        print(f"WARNING: unable to fetch O-A0058-005 DateTime; frontend will use fallback: {exc}")

    frames = []
    for dt, url in records:
        if dt < start or dt > now + timedelta(minutes=10):
            continue
        stamp = dt.strftime("%Y%m%d%H%M")
        filename = f"radar_{stamp}.png"
        target = OUT / filename
        if not target.exists():
            download_url = url
            parsed_url = urlparse(download_url)
            query = parse_qs(parsed_url.query)
            if "Authorization" not in query:
                from urllib.parse import urlencode, urlunparse
                query["Authorization"] = [API_KEY]
                download_url = urlunparse(parsed_url._replace(query=urlencode(query, doseq=True)))
            r = SESSION.get(download_url, timeout=120)
            r.raise_for_status()
            raw = r.content
            actual_dt, vals, nx, ny, lon0, lat0, res = parse_grid(raw)
            render_png(vals, nx, ny, target)
            dt = actual_dt
        # O-A0059-001 documented grid: origin 115.0E/18.0N, 0.0125 degree,
        # 921 columns × 881 rows. PNG is north-up; Leaflet bounds use south-west/north-east.
        frames.append({
            "time": dt.isoformat(),
            "url": f"./{filename}",
            "bounds": [[18.0, 115.0], [29.0, 126.5]]
        })
    # Keep only files in the requested 24h window; don't delete anything outside this directory.
    keep = {Path(f["url"]).name for f in frames}
    for p in OUT.glob("radar_*.png"):
        if p.name not in keep:
            p.unlink()
    frames.sort(key=lambda f: f["time"])
    manifest = {
        "source": "Central Weather Administration O-A0059-001 historical radar reflectivity grid",
        "data_id": DATA_ID,
        "updated_at": now.isoformat(),
        "window_hours": 24,
        "cadence_minutes": 10,
        "frames": frames
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Published {len(frames)} official radar frames from {start.isoformat()} to {now.isoformat()}")

if __name__ == "__main__":
    main()

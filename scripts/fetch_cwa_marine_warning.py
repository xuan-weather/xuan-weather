#!/usr/bin/env python3
"""Fetch CWA official typhoon-message KMZ and export only explicitly labelled warning geometries.
If the official KMZ has no identifiable warning geometry, write an empty FeatureCollection.
Never fabricate a boundary from textual sea-area descriptions.
"""
from __future__ import annotations
import io, json, os, re, sys, urllib.request, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

API_KEY = os.environ.get("CWA_API_KEY", "").strip()
URL = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-002"
OUT = Path("data/cwa-marine-warning.geojson")

if not API_KEY:
    sys.exit("ERROR: 請在 GitHub Repository Secrets 設定 CWA_API_KEY（中央氣象署 Open Data API 金鑰）。")

req = urllib.request.Request(
    f"{URL}?Authorization={API_KEY}&format=KMZ",
    headers={"User-Agent": "XuanWeather/1.0", "Accept": "application/octet-stream"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
except Exception as exc:
    sys.exit(f"ERROR: 下載中央氣象署颱風消息 KMZ 失敗：{exc}")

try:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ 內找不到 KML")
        # Parse every KML file present, retaining official labels and folder context.
        roots = [ET.fromstring(zf.read(name)) for name in kml_names]
except Exception as exc:
    sys.exit(f"ERROR: 官方 KMZ 格式無法解析：{exc}")

NS = {"k": "http://www.opengis.net/kml/2.2"}
WARNING_RE = re.compile(r"警戒|警報區域|警戒區域|警戒範圍|warning\s*(area|zone|line)|marine\s*warning", re.I)

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def text_of(node: ET.Element) -> str:
    return " ".join((t.strip() for t in node.itertext() if t and t.strip()))

def parse_coords(text: str):
    points = []
    for item in text.strip().split():
        parts = item.split(",")
        if len(parts) >= 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                points.append([lon, lat])
            except ValueError:
                continue
    return points

def geometry_from_node(node: ET.Element):
    tag = local_name(node.tag)
    if tag == "Polygon":
        rings = []
        for child in node.iter():
            if local_name(child.tag) == "coordinates" and child.text:
                coords = parse_coords(child.text)
                if len(coords) >= 4:
                    rings.append(coords)
        if rings:
            return {"type": "Polygon", "coordinates": rings}
    elif tag == "LineString":
        for child in node.iter():
            if local_name(child.tag) == "coordinates" and child.text:
                coords = parse_coords(child.text)
                if len(coords) >= 2:
                    return {"type": "LineString", "coordinates": coords}
    elif tag == "MultiGeometry":
        geoms = []
        for child in list(node):
            geom = geometry_from_node(child)
            if geom:
                geoms.append(geom)
        if geoms:
            # Keep mixed official line/polygon geometries in a valid GeometryCollection.
            return {"type": "GeometryCollection", "geometries": geoms}
    return None

features = []
for root in roots:
    # Include parent Folder names in context, because KML labels may be stored on folders.
    def walk(node: ET.Element, parents: list[str]):
        tag = local_name(node.tag)
        current = parents[:]
        if tag == "Folder":
            name = next((text_of(c) for c in list(node) if local_name(c.tag) == "name"), "")
            if name:
                current.append(name)
        if tag == "Placemark":
            name = next((text_of(c) for c in list(node) if local_name(c.tag) == "name"), "")
            desc = " ".join(text_of(c) for c in list(node) if local_name(c.tag) in ("description", "Snippet", "ExtendedData"))
            context = " ".join(current + [name, desc])
            if WARNING_RE.search(context):
                geom = None
                for child in list(node):
                    if local_name(child.tag) in ("Polygon", "LineString", "MultiGeometry"):
                        geom = geometry_from_node(child)
                        if geom:
                            break
                if geom:
                    features.append({
                        "type": "Feature",
                        "properties": {"name": name or "中央氣象署颱風警戒區域", "source": "中央氣象署官方颱風消息 KMZ"},
                        "geometry": geom,
                    })
        for child in list(node):
            walk(child, current)
    walk(root, [])

# Empty is intentional and safer than guessing if official KMZ provides only text or track data.
result = {
    "type": "FeatureCollection",
    "name": "CWA official marine typhoon warning geometry",
    "metadata": {
        "source": "中央氣象署 W-C0034-002 KMZ",
        "generated_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "warning_geometry_count": len(features),
        "note": "若 count 為 0，代表 KMZ 內未找到可明確識別的警戒區域幾何；不以文字海域或固定座標代畫。",
    },
    "features": features,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
print(f"寫入 {OUT}；可識別的官方警戒幾何數量：{len(features)}")
if not features:
    print("INFO: 官方 KMZ 未提供可明確識別的警戒線/警戒區域幾何；前端將安全地不顯示白色警戒線。")

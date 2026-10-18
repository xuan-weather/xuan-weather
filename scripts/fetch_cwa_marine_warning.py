#!/usr/bin/env python3
"""Fetch CWA typhoon message KMZ for any explicit warning geometry.

Important:
- W-C0034-002 (颱風消息) is requested as KMZ for KML geometry parsing.
- W-C0034-001 (颱風警報) is NOT requested as KMZ; that request returned HTTP 404.
  The script checks the warning product as CAP for status/diagnostics only.
- It never fabricates a warning line from a textual sea-area description.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

API_KEY = os.environ.get("CWA_API_KEY", "").strip()
OUT = Path("data/cwa-marine-warning.geojson")
BASE = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"

if not API_KEY:
    sys.exit("ERROR: 請在 GitHub Repository Secrets 設定 CWA_API_KEY。")

def download(data_id: str, fmt: str) -> bytes:
    url = f"{BASE}/{data_id}?Authorization={API_KEY}&format={fmt}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "XuanWeather/1.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()

# 1) CAP is the warning-status product format; do not request KMZ from W-C0034-001.
warning_cap = None
warning_status_note = ""
try:
    warning_cap = download("W-C0034-001", "CAP")
    warning_status_note = "已成功取得 W-C0034-001 CAP 警報資料（僅供狀態/診斷，不含可直接繪圖的 GeoJSON）。"
except Exception as exc:
    warning_status_note = f"W-C0034-001 CAP 下載失敗：{exc}"
    print(f"WARNING: {warning_status_note}")

# 2) KMZ geometry parsing uses the product that the previous workflow already used.
try:
    raw = download("W-C0034-002", "KMZ")
except Exception as exc:
    sys.exit(f"ERROR: 下載中央氣象署 W-C0034-002 颱風消息 KMZ 失敗：{exc}")

try:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        kml_names = [name for name in zf.namelist() if name.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ 內找不到 KML")
        roots = [ET.fromstring(zf.read(name)) for name in kml_names]
except Exception as exc:
    sys.exit(f"ERROR: W-C0034-002 KMZ 格式無法解析：{exc}")

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def text_of(node: ET.Element) -> str:
    return " ".join(t.strip() for t in node.itertext() if t and t.strip())

def parse_coords(value: str):
    points = []
    for item in value.strip().split():
        parts = item.split(",")
        if len(parts) >= 2:
            try:
                points.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    return points

def geometry_from_node(node: ET.Element):
    tag = local_name(node.tag)
    if tag == "Polygon":
        rings = []
        for ring in node.iter():
            if local_name(ring.tag) == "LinearRing":
                coords_node = next(
                    (child for child in ring.iter()
                     if local_name(child.tag) == "coordinates" and child.text),
                    None,
                )
                if coords_node is not None:
                    coords = parse_coords(coords_node.text or "")
                    if len(coords) >= 4:
                        rings.append(coords)
        return {"type": "Polygon", "coordinates": rings} if rings else None
    if tag == "LineString":
        coords_node = next(
            (child for child in node.iter()
             if local_name(child.tag) == "coordinates" and child.text),
            None,
        )
        if coords_node is not None:
            coords = parse_coords(coords_node.text or "")
            if len(coords) >= 2:
                return {"type": "LineString", "coordinates": coords}
        return None
    if tag == "MultiGeometry":
        geometries = [g for child in list(node) if (g := geometry_from_node(child))]
        return {"type": "GeometryCollection", "geometries": geometries} if geometries else None
    return None

WARNING_RE = re.compile(
    r"海上颱風警報|海上警報|警戒區域|警戒範圍|警報區域|"
    r"warning\s*(area|zone|line)|marine\s*warning",
    re.I,
)

features = []
placemarks = []

for root in roots:
    def walk(node: ET.Element, parents: list[str]):
        tag = local_name(node.tag)
        current = parents[:]
        if tag == "Folder":
            folder_name = next(
                (text_of(c) for c in list(node) if local_name(c.tag) == "name"),
                "",
            )
            if folder_name:
                current.append(folder_name)
        if tag == "Placemark":
            name = next(
                (text_of(c) for c in list(node) if local_name(c.tag) == "name"),
                "",
            )
            desc = " ".join(
                text_of(c) for c in list(node)
                if local_name(c.tag) in ("description", "Snippet", "ExtendedData")
            )
            context = " ".join(current + [name, desc])
            geom = None
            for child in list(node):
                if local_name(child.tag) in ("Polygon", "LineString", "MultiGeometry"):
                    geom = geometry_from_node(child)
                    if geom:
                        break
            placemarks.append({
                "name": name,
                "has_geometry": bool(geom),
                "warning_label_match": bool(WARNING_RE.search(context)),
            })
            if geom and WARNING_RE.search(context):
                features.append({
                    "type": "Feature",
                    "properties": {
                        "name": name or "中央氣象署官方警戒區域",
                        "source": "中央氣象署 W-C0034-002 KMZ（需人工確認是否為警戒幾何）",
                    },
                    "geometry": geom,
                })
        for child in list(node):
            walk(child, current)
    walk(root, [])

result = {
    "type": "FeatureCollection",
    "name": "CWA typhoon warning geometry diagnostics",
    "metadata": {
        "geometry_source": "W-C0034-002 KMZ",
        "warning_status_source": "W-C0034-001 CAP",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "warning_geometry_count": len(features),
        "placemark_count": len(placemarks),
        "placemarks_with_geometry": sum(1 for p in placemarks if p["has_geometry"]),
        "warning_label_matches": sum(1 for p in placemarks if p["warning_label_match"]),
        "warning_cap_status": warning_status_note,
        "note": (
            "W-C0034-001 不再以 KMZ 下載，避免 HTTP 404。"
            "白色虛線仍只會在 KMZ 中找到明確可辨識的官方警戒幾何時出現；"
            "如果 count=0，請依診斷欄位進一步調整資料解析，不可自行編造座標。"
        ),
    },
    "features": features,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
print(
    "完成 GeoJSON："
    f"Placemark={result['metadata']['placemark_count']}，"
    f"含幾何={result['metadata']['placemarks_with_geometry']}，"
    f"警戒名稱符合={result['metadata']['warning_label_matches']}，"
    f"輸出幾何={len(features)}"
)
print(warning_status_note)
if not features:
    print("INFO: 本次未輸出警戒幾何；工作流程可成功完成，但地圖不會憑空出現白色警戒線。")

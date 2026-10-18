#!/usr/bin/env python3
"""Fetch CWA official typhoon WARNING KMZ and export explicitly labelled warning geometries.

Source: W-C0034-001 (颱風警報), not W-C0034-002 (颱風消息).
If the official warning product has no identifiable warning geometry, write an empty
FeatureCollection rather than fabricating a boundary from text or fixed coordinates.
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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

API_KEY = os.environ.get("CWA_API_KEY", "").strip()
DATA_ID = "W-C0034-001"
URL = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/{DATA_ID}"
OUT = Path("data/cwa-marine-warning.geojson")

if not API_KEY:
    sys.exit(
        "ERROR: 請在 GitHub Repository Secrets 設定 CWA_API_KEY "
        "（中央氣象署 Open Data API 金鑰）。"
    )

req = urllib.request.Request(
    f"{URL}?Authorization={API_KEY}&format=KMZ",
    headers={
        "User-Agent": "XuanWeather/1.0",
        "Accept": "application/octet-stream",
    },
)
try:
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
except Exception as exc:
    sys.exit(f"ERROR: 下載中央氣象署 {DATA_ID} 颱風警報 KMZ 失敗：{exc}")

try:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        kml_names = [name for name in zf.namelist() if name.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ 內找不到 KML")
        roots = [ET.fromstring(zf.read(name)) for name in kml_names]
except Exception as exc:
    sys.exit(f"ERROR: 官方 KMZ 格式無法解析：{exc}")

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def text_of(node: ET.Element) -> str:
    return " ".join(t.strip() for t in node.itertext() if t and t.strip())

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
        # Parse each LinearRing independently; preserve outer and inner rings.
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
        if rings:
            return {"type": "Polygon", "coordinates": rings}
    elif tag == "LineString":
        coords_node = next(
            (child for child in node.iter()
             if local_name(child.tag) == "coordinates" and child.text),
            None,
        )
        if coords_node is not None:
            coords = parse_coords(coords_node.text or "")
            if len(coords) >= 2:
                return {"type": "LineString", "coordinates": coords}
    elif tag == "MultiGeometry":
        geometries = []
        for child in list(node):
            geometry = geometry_from_node(child)
            if geometry:
                geometries.append(geometry)
        if geometries:
            return {"type": "GeometryCollection", "geometries": geometries}
    return None

# Only geometries explicitly identified as warning-related are exported.
WARNING_RE = re.compile(
    r"警戒|警報區域|警戒區域|警戒範圍|海上警報|海上警戒|"
    r"warning\s*(area|zone|line)|marine\s*warning",
    re.I,
)

features = []
placemark_diagnostics = []

for root in roots:
    def walk(node: ET.Element, parents: list[str]):
        tag = local_name(node.tag)
        current = parents[:]
        if tag == "Folder":
            folder_name = next(
                (text_of(child) for child in list(node)
                 if local_name(child.tag) == "name"),
                "",
            )
            if folder_name:
                current.append(folder_name)

        if tag == "Placemark":
            name = next(
                (text_of(child) for child in list(node)
                 if local_name(child.tag) == "name"),
                "",
            )
            desc = " ".join(
                text_of(child) for child in list(node)
                if local_name(child.tag) in ("description", "Snippet", "ExtendedData")
            )
            context = " ".join(current + [name, desc])
            geometry = None
            for child in list(node):
                if local_name(child.tag) in ("Polygon", "LineString", "MultiGeometry"):
                    geometry = geometry_from_node(child)
                    if geometry:
                        break

            placemark_diagnostics.append({
                "name": name,
                "has_geometry": bool(geometry),
                "warning_label_match": bool(WARNING_RE.search(context)),
            })

            if geometry and WARNING_RE.search(context):
                features.append({
                    "type": "Feature",
                    "properties": {
                        "name": name or "中央氣象署官方颱風警報區域",
                        "source": f"中央氣象署 {DATA_ID} 官方颱風警報 KMZ",
                    },
                    "geometry": geometry,
                })

        for child in list(node):
            walk(child, current)

    walk(root, [])

result = {
    "type": "FeatureCollection",
    "name": "CWA official marine typhoon warning geometry",
    "metadata": {
        "source": f"中央氣象署 {DATA_ID} 颱風警報 KMZ",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "warning_geometry_count": len(features),
        "placemark_count": len(placemark_diagnostics),
        "placemarks_with_geometry": sum(
            1 for item in placemark_diagnostics if item["has_geometry"]
        ),
        "warning_label_matches": sum(
            1 for item in placemark_diagnostics if item["warning_label_match"]
        ),
        "note": (
            "資料來源已改為 W-C0034-001 颱風警報。只有可辨識的官方警戒區域幾何才會輸出；"
            "若數量為 0，請檢查 Actions log 的 Placemark 診斷資訊，"
            "不可自行以文字海域或固定座標代畫。"
        ),
    },
    "features": features,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
print(
    f"寫入 {OUT}；來源={DATA_ID}；"
    f"Placemark={len(placemark_diagnostics)}；"
    f"含幾何={result['metadata']['placemarks_with_geometry']}；"
    f"警戒標籤符合={result['metadata']['warning_label_matches']}；"
    f"輸出警戒幾何={len(features)}"
)
if not features:
    print(
        "INFO: 目前沒有符合條件的官方警戒區域幾何；"
        "前端不會繪製白色警戒線。請查看上述診斷數量確認官方 KMZ 結構。"
    )

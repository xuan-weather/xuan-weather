#!/usr/bin/env python3
"""Fetch CWA typhoon products and diagnose available official warning geometries.

This script does not invent or approximate a warning boundary.
It downloads W-C0034-001 as CAP for warning-status diagnostics and
W-C0034-002 as KMZ to inspect its KML geometries.
Every Placemark is recorded in data/cwa-marine-warning.geojson metadata,
including names, folder path, geometry type, point count, bounding box,
and sample coordinates. Only geometries whose descriptive text explicitly
matches warning-area terms are emitted as candidate features.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_KEY = os.environ.get("CWA_API_KEY", "").strip()
OUT = Path("data/cwa-marine-warning.geojson")
BASE = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"

if not API_KEY:
    sys.exit("ERROR: 請在 GitHub Repository Secrets 設定 CWA_API_KEY。")


def download(data_id: str, fmt: str) -> bytes:
    """Download one CWA open-data product."""
    url = f"{BASE}/{data_id}?Authorization={API_KEY}&format={fmt}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "XuanWeather/1.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


# 1) Warning product: use CAP for status/diagnostics, not KMZ.
warning_status_note = ""
try:
    warning_cap = download("W-C0034-001", "CAP")
    warning_status_note = (
        "已成功取得 W-C0034-001 CAP 警報資料；"
        f"下載大小 {len(warning_cap)} bytes。CAP 內容不會在本程式中被假設為 GeoJSON。"
    )
except Exception as exc:
    warning_status_note = f"W-C0034-001 CAP 下載失敗：{exc}"
    print(f"WARNING: {warning_status_note}")


# 2) Download the typhoon-message KMZ and inspect its KML geometry.
try:
    raw = download("W-C0034-002", "KMZ")
except Exception as exc:
    sys.exit(f"ERROR: 下載中央氣象署 W-C0034-002 颱風消息 KMZ 失敗：{exc}")

try:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        kml_names = [
            name for name in zf.namelist()
            if name.lower().endswith(".kml")
        ]
        if not kml_names:
            raise ValueError("KMZ 內找不到 KML")
        roots = [ET.fromstring(zf.read(name)) for name in kml_names]
except Exception as exc:
    sys.exit(f"ERROR: W-C0034-002 KMZ 格式無法解析：{exc}")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_of(node: ET.Element) -> str:
    return " ".join(
        value.strip()
        for value in node.itertext()
        if value and value.strip()
    )


def parse_coords(value: str) -> list[list[float]]:
    """Parse KML coordinates into [longitude, latitude] pairs."""
    points: list[list[float]] = []
    for item in value.strip().split():
        parts = item.split(",")
        if len(parts) >= 2:
            try:
                points.append([float(parts[0]), float(parts[1])])
            except ValueError:
                continue
    return points


def geometry_from_node(node: ET.Element) -> dict[str, Any] | None:
    tag = local_name(node.tag)

    if tag == "Polygon":
        rings: list[list[list[float]]] = []
        for ring in node.iter():
            if local_name(ring.tag) != "LinearRing":
                continue
            coords_node = next(
                (
                    child for child in ring.iter()
                    if local_name(child.tag) == "coordinates" and child.text
                ),
                None,
            )
            if coords_node is not None:
                coords = parse_coords(coords_node.text or "")
                if len(coords) >= 4:
                    rings.append(coords)
        return {"type": "Polygon", "coordinates": rings} if rings else None

    if tag == "LineString":
        coords_node = next(
            (
                child for child in node.iter()
                if local_name(child.tag) == "coordinates" and child.text
            ),
            None,
        )
        if coords_node is not None:
            coords = parse_coords(coords_node.text or "")
            if len(coords) >= 2:
                return {"type": "LineString", "coordinates": coords}
        return None

    if tag == "MultiGeometry":
        geometries = [
            geometry
            for child in list(node)
            if (geometry := geometry_from_node(child)) is not None
        ]
        return (
            {"type": "GeometryCollection", "geometries": geometries}
            if geometries else None
        )

    return None


WARNING_RE = re.compile(
    r"海上颱風警報|海上警報|警戒區域|警戒範圍|警報區域|"
    r"warning\s*(area|zone|line)|marine\s*warning",
    re.I,
)


def geometry_points(geometry: dict[str, Any] | None) -> list[list[float]]:
    """Flatten geometry coordinates into [longitude, latitude] pairs."""
    points: list[list[float]] = []

    def walk_coords(obj: Any) -> None:
        if not isinstance(obj, list):
            return
        if (
            len(obj) >= 2
            and isinstance(obj[0], (int, float))
            and isinstance(obj[1], (int, float))
        ):
            points.append([float(obj[0]), float(obj[1])])
            return
        for item in obj:
            walk_coords(item)

    if not geometry:
        return points

    if geometry.get("type") == "GeometryCollection":
        for child in geometry.get("geometries", []):
            walk_coords(child.get("coordinates", []))
    else:
        walk_coords(geometry.get("coordinates", []))

    return points


features: list[dict[str, Any]] = []
placemarks: list[dict[str, Any]] = []

for root in roots:

    def walk(node: ET.Element, parents: list[str]) -> None:
        tag = local_name(node.tag)
        current = parents[:]

        if tag == "Folder":
            folder_name = next(
                (
                    text_of(child) for child in list(node)
                    if local_name(child.tag) == "name"
                ),
                "",
            )
            if folder_name:
                current.append(folder_name)

        if tag == "Placemark":
            name = next(
                (
                    text_of(child) for child in list(node)
                    if local_name(child.tag) == "name"
                ),
                "",
            )
            desc = " ".join(
                text_of(child)
                for child in list(node)
                if local_name(child.tag) in (
                    "description", "Snippet", "ExtendedData"
                )
            )
            context = " ".join(current + [name, desc])

            geom = None
            for child in list(node):
                if local_name(child.tag) in (
                    "Polygon", "LineString", "MultiGeometry"
                ):
                    geom = geometry_from_node(child)
                    if geom:
                        break

            points = geometry_points(geom)
            bbox = None
            if points:
                longitudes = [point[0] for point in points]
                latitudes = [point[1] for point in points]
                bbox = [
                    min(longitudes),
                    min(latitudes),
                    max(longitudes),
                    max(latitudes),
                ]

            label_match = bool(WARNING_RE.search(context))
            placemarks.append({
                "name": name,
                "folder_path": " / ".join(current),
                "description": desc[:500],
                "has_geometry": bool(geom),
                "geometry_type": geom.get("type") if geom else None,
                "point_count": len(points),
                "bbox_lonlat": bbox,
                "sample_coordinates_lonlat": points[:8],
                "warning_label_match": label_match,
            })

            # Candidate features only: never manufacture a boundary.
            # A human should inspect the diagnostics before trusting these.
            if geom and label_match:
                features.append({
                    "type": "Feature",
                    "properties": {
                        "name": name or "中央氣象署官方警戒區域候選項目",
                        "folder_path": " / ".join(current),
                        "source": "中央氣象署 W-C0034-002 KMZ（候選項目，需核對）",
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
        "placemarks_with_geometry": sum(
            1 for placemark in placemarks if placemark["has_geometry"]
        ),
        "warning_label_matches": sum(
            1 for placemark in placemarks
            if placemark["warning_label_match"]
        ),
        "warning_cap_status": warning_status_note,
        # This is the key addition: detailed diagnostics for every Placemark.
        "placemarks_diagnostics": placemarks,
        "note": (
            "本檔會列出 KMZ 每個 Placemark 的名稱、資料夾、幾何類型、"
            "點數、經緯度範圍及部分座標。warning_geometry_count 僅表示"
            "名稱文字符合關鍵字的候選幾何，不代表已確認為官方警戒外框。"
            "若為 0，請先查看 placemarks_diagnostics，不能自行編造座標。"
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
    "完成 GeoJSON 診斷："
    f"Placemark={result['metadata']['placemark_count']}，"
    f"含幾何={result['metadata']['placemarks_with_geometry']}，"
    f"名稱符合關鍵字={result['metadata']['warning_label_matches']}，"
    f"輸出候選幾何={result['metadata']['warning_geometry_count']}"
)
print(warning_status_note)
print(f"診斷資料已寫入：{OUT}")
if not features:
    print(
        "INFO: 沒有輸出符合關鍵字的候選警戒幾何；"
        "請檢查 placemarks_diagnostics，不要以固定座標代畫。"
    )

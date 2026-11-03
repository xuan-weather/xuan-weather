#!/usr/bin/env python3
"""Merge the CWA E-A0073-001 catalog into the local earthquake archive.

Records are deduplicated across both nested and flat CWA field layouts.
The nested EarthquakeInfo/Epicenter/EarthquakeMagnitude layout is preferred
because it is consumed by the site's historical-earthquake UI.
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "中央氣象署地震目錄 E-A0073-001"


def lname(tag):
    return tag.rsplit("}", 1)[-1]


def convert(node):
    children = list(node)
    if not children:
        return (node.text or "").strip()
    result = {}
    for child in children:
        key, value = lname(child.tag), convert(child)
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result


def earthquake_info(record):
    if not isinstance(record, dict):
        return {}
    info = record.get("EarthquakeInfo")
    return info if isinstance(info, dict) else record


def first_value(*values):
    for value in values:
        if value is not None and str(value).strip() != "":
            return value
    return ""


def number(value, digits=3):
    try:
        return f"{float(str(value).replace('°', '').strip()):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def event_key(record):
    """Stable event key for nested and flat catalog records."""
    info = earthquake_info(record)
    epicenter = info.get("Epicenter")
    epicenter = epicenter if isinstance(epicenter, dict) else {}
    magnitude = info.get("EarthquakeMagnitude")
    magnitude = magnitude if isinstance(magnitude, dict) else {}

    origin = str(first_value(info.get("OriginTime"), info.get("originTime"))).strip()
    lat = number(first_value(info.get("EpicenterLatitude"), epicenter.get("EpicenterLatitude")))
    lon = number(first_value(info.get("EpicenterLongitude"), epicenter.get("EpicenterLongitude")))
    mag = number(first_value(info.get("LocalMagnitude"), magnitude.get("MagnitudeValue")), 1)

    # Coordinates + origin time + magnitude distinguish events reliably.
    if origin and lat and lon and mag:
        return (origin, lat, lon, mag)

    # Conservative fallback for older records that lack numeric fields.
    location = str(first_value(epicenter.get("Location"), info.get("Location"))).strip()
    if origin and location:
        return (origin, location, mag)
    return None


def richness(record):
    """Prefer the site's nested CWA schema; use field completeness as tie-breaker."""
    info = earthquake_info(record)
    populated = sum(v not in (None, "", {}, []) for v in info.values())
    nested_bonus = 100 if isinstance(info.get("Epicenter"), dict) else 0
    magnitude_bonus = 20 if isinstance(info.get("EarthquakeMagnitude"), dict) else 0
    return nested_bonus + magnitude_bonus + populated


def merge_records(records):
    by_key = {}
    unkeyed = []
    duplicates = 0
    for row in records:
        if not isinstance(row, dict):
            continue
        key = event_key(row)
        if key is None:
            # Do not silently discard records that cannot be safely matched.
            unkeyed.append(row)
            continue
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = row
        else:
            duplicates += 1
            if richness(row) > richness(previous):
                by_key[key] = row
    return list(by_key.values()) + unkeyed, duplicates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    src, dst = Path(args.current), Path(args.output)

    if not src.is_file() or src.stat().st_size < 100:
        raise SystemExit("地震目錄檔不存在或大小異常，停止更新。")
    try:
        root = ET.parse(src).getroot()
    except ET.ParseError as exc:
        raise SystemExit(f"XML 解析失敗：{exc}")
    if lname(root.tag).lower() != "cwaopendata":
        raise SystemExit("XML 根節點不符預期，停止更新。")

    nodes = [n for n in root.iter() if lname(n.tag) == "EarthquakeInfo"]
    if not nodes:
        raise SystemExit("XML 沒有 EarthquakeInfo，拒絕空資料覆蓋。")

    incoming = []
    for node in nodes:
        info = convert(node)
        if isinstance(info, dict) and info.get("OriginTime"):
            incoming.append({
                "EarthquakeInfo": info,
                "_quakeType": "historical",
                "_catalogSource": SOURCE,
            })
    if not incoming:
        raise SystemExit("XML 沒有有效地震時間，停止更新。")

    old = {}
    if dst.exists():
        try:
            old = json.loads(dst.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"既有 JSON 無法解析，保護舊資料並停止：{exc}")
    old_rows = old.get("earthquakes", [])
    if not isinstance(old_rows, list):
        raise SystemExit("既有 earthquakes 欄位格式錯誤，停止更新。")

    # Deduplicate old + incoming together so historical duplicates already
    # present in the JSON are cleaned during the next successful sync.
    rows, duplicate_count = merge_records(old_rows + incoming)
    old_unique, _ = merge_records(old_rows)
    if len(rows) < len(old_unique):
        raise SystemExit("合併後筆數少於既有唯一資料，拒絕覆蓋。")
    if not rows:
        raise SystemExit("合併結果為空，拒絕覆蓋。")

    rows.sort(
        key=lambda r: str(earthquake_info(r).get("OriginTime") or ""),
        reverse=True,
    )
    payload = {
        "source": SOURCE,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "recordCount": len(rows),
        "deduplication": {
            "method": "OriginTime + EpicenterLatitude + EpicenterLongitude + MagnitudeValue",
            "mergedInputRecordCount": len(old_rows) + len(incoming),
            "removedDuplicateCount": duplicate_count,
            "note": "同一地震的巢狀與扁平欄位格式會合併；無法安全判斷的紀錄會保留。",
        },
        "earthquakes": rows,
    }

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    check = json.loads(tmp.read_text(encoding="utf-8"))
    if not check.get("earthquakes") or check["recordCount"] != len(check["earthquakes"]):
        raise SystemExit("輸出 JSON 驗證失敗，保留原始檔。")
    tmp.replace(dst)
    print(
        f"完成：舊資料 {len(old_rows)} 筆；本次下載 {len(incoming)} 筆；"
        f"移除重複 {duplicate_count} 筆；合併後 {len(rows)} 筆。"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"同步錯誤：{exc}", file=sys.stderr)
        raise

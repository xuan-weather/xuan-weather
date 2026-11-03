#!/usr/bin/env python3
import argparse, json, sys, xml.etree.ElementTree as ET
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

def key(record):
    info = record.get("EarthquakeInfo") or {}
    epi = info.get("Epicenter") or {}
    return "|".join(str(x or "") for x in (
        info.get("OriginTime"), epi.get("EpicenterLatitude"),
        epi.get("EpicenterLongitude"), epi.get("Location")
    ))

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
            incoming.append({"EarthquakeInfo": info, "_quakeType": "historical",
                             "_catalogSource": SOURCE})
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
    merged = {}
    for row in old_rows + incoming:
        if isinstance(row, dict):
            k = key(row)
            if k.strip("|"):
                merged[k] = row
    rows = sorted(merged.values(), key=lambda r: str(
        (r.get("EarthquakeInfo") or {}).get("OriginTime") or ""), reverse=True)
    if len(rows) < len(old_rows):
        raise SystemExit("合併後筆數少於舊資料，拒絕覆蓋。")
    payload = {"source": SOURCE, "updatedAt": datetime.now(timezone.utc).isoformat(),
               "recordCount": len(rows), "earthquakes": rows}
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    check = json.loads(tmp.read_text(encoding="utf-8"))
    if not check.get("earthquakes") or check["recordCount"] != len(check["earthquakes"]):
        raise SystemExit("輸出 JSON 驗證失敗，保留原始檔。")
    tmp.replace(dst)
    print(f"完成：舊資料 {len(old_rows)} 筆；本次 {len(incoming)} 筆；合併後 {len(rows)} 筆。")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"同步錯誤：{exc}", file=sys.stderr)
        raise

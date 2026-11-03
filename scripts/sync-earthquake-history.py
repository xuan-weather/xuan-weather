#!/usr/bin/env python3
"""Normalize CWA E-A0073-001 XML and optional annual historical XML files."""
import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def child_text(node, wanted):
    for child in list(node):
        if local_name(child.tag) == wanted:
            return (child.text or '').strip()
    return ''


def number(value):
    try:
        n = float(str(value).strip())
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def parse_xml(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise RuntimeError(f'{path} XML 格式錯誤：{exc}') from exc

    found = []
    for node in root.iter():
        if local_name(node.tag) != 'EarthquakeInfo':
            continue
        origin = child_text(node, 'OriginTime')
        lat = number(child_text(node, 'EpicenterLatitude'))
        lon = number(child_text(node, 'EpicenterLongitude'))
        depth = number(child_text(node, 'FocalDepth'))
        magnitude = number(child_text(node, 'LocalMagnitude'))
        if not origin or lat is None or lon is None:
            continue
        location = f'{lat:.3f}°N, {lon:.3f}°E'
        info = {
            'OriginTime': origin,
            'Epicenter': {
                'Location': location,
                'EpicenterLatitude': lat,
                'EpicenterLongitude': lon,
            },
            'FocalDepth': depth,
            'EarthquakeMagnitude': {'MagnitudeValue': magnitude},
        }
        for field in ('StationNumber', 'PhaseNumber', 'MinimumEpicenterDistance', 'Quality', 'ReviewStatus'):
            value = child_text(node, field)
            if value:
                info[field] = value
        found.append({
            'EarthquakeInfo': info,
            '_quakeType': 'historical',
            '_catalogSource': '中央氣象署地震目錄 E-A0073-001',
        })
    return found


def event_key(eq):
    info = eq.get('EarthquakeInfo') or {}
    epi = info.get('Epicenter') or {}
    return '|'.join([
        str(info.get('OriginTime') or eq.get('OriginTime') or ''),
        str(epi.get('EpicenterLatitude') or ''),
        str(epi.get('EpicenterLongitude') or ''),
        str((info.get('EarthquakeMagnitude') or {}).get('MagnitudeValue') or ''),
    ])


def load_existing(path):
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'既有歷史資料 JSON 無法讀取，停止覆寫：{exc}') from exc
    if isinstance(payload, list):
        return payload
    for key in ('earthquakes', 'Earthquake', 'data', 'records'):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, list):
            return value
        if key == 'records' and isinstance(value, dict) and isinstance(value.get('Earthquake'), list):
            return value['Earthquake']
    raise RuntimeError('既有 earthquake-history.json 結構不明，停止覆寫以避免遺失資料。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--current', required=True)
    ap.add_argument('--archive-dir', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    current = Path(args.current)
    archive_dir = Path(args.archive_dir)
    output = Path(args.output)
    if not current.is_file() or current.stat().st_size == 0:
        raise SystemExit('找不到中央氣象署本年度地震目錄 XML。')

    incoming = parse_xml(current)
    archive_files = sorted(archive_dir.rglob('*.xml')) if archive_dir.exists() else []
    for xml_path in archive_files:
        incoming.extend(parse_xml(xml_path))

    existing = load_existing(output)
    merged = {}
    for eq in existing + incoming:
        key = event_key(eq)
        if key.strip('|'):
            merged[key] = eq
    records = list(merged.values())
    records.sort(key=lambda eq: str((eq.get('EarthquakeInfo') or {}).get('OriginTime') or ''), reverse=True)

    if not records:
        raise SystemExit('沒有解析到任何地震事件；拒絕寫入空資料庫。')

    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'source': '中央氣象署地震目錄 E-A0073-001／年度歷史地震目錄',
        'updatedAt': datetime.now(timezone.utc).isoformat(),
        'recordCount': len(records),
        'earthquakes': records,
    }
    tmp = output.with_suffix(output.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(output)
    print(f'本年度目錄解析：{len(parse_xml(current))} 筆')
    print(f'年度歷史 XML 檔案：{len(archive_files)} 個')
    print(f'合併後資料庫：{len(records)} 筆')
    print(f'輸出：{output}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'同步失敗：{exc}', file=sys.stderr)
        raise

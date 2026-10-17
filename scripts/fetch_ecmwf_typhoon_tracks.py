#!/usr/bin/env python3
"""Download official ECMWF TC-track BUFR products; never invent coordinates."""
import json, subprocess, tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path('data/typhoon-model-tracks.json')
REGION = {'west': 108, 'south': 4, 'east': 145, 'north': 36}
MODELS = {
    'ECMWF IFS': {'model': 'ifs', 'stream': 'oper', 'step': 240},
    'ECMWF AIFS': {'model': 'aifs-single', 'stream': 'oper', 'step': 240},
    'AIFS ENS': {'model': 'aifs-ens', 'stream': 'enfo', 'step': 240},
}


def candidates(now):
    hour = (now.hour // 6) * 6
    return [((now.replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(hours=back)))
            for back in range(0, 36, 6)]


def download(cfg, cycle, target):
    from ecmwf.opendata import Client
    steps = [cfg['step']]
    # ECMWF IFS 06/18 UTC operational TC-track products use shorter forecast step.
    if cfg['model'] == 'ifs' and cycle.hour in (6, 18):
        steps = [90]
    last = None
    for step in steps:
        try:
            kwargs = dict(date=cycle.strftime('%Y%m%d'), time=cycle.hour,
                          stream=cfg['stream'], type='tf', step=step, target=str(target))
            if cfg['model'] != 'ifs':
                kwargs['model'] = cfg['model']
            Client(source='ecmwf').retrieve(**kwargs)
            if target.exists() and target.stat().st_size:
                return step
        except Exception as exc:
            last = exc
    raise RuntimeError(f'產品尚不可用：{last}')


def decode_bufr(path, model_name, cycle):
    """Conservative first-pass decoder. Only publish points if named coordinate arrays match."""
    raw = subprocess.check_output(['bufr_dump', '-j', str(path)], text=True)
    data = json.loads(raw)
    records = []
    def walk(obj):
        if isinstance(obj, dict):
            if 'key' in obj and 'value' in obj:
                records.append((str(obj['key']).lower().split('#')[0], obj['value']))
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(data)
    def nums(names):
        out = []
        for key, val in records:
            if key in names and isinstance(val, (int, float)):
                out.append(float(val))
        return out
    lat = nums({'latitude', 'latitude of the center'})
    lon = nums({'longitude', 'longitude of the center'})
    # Do not infer storm IDs, time steps, or coordinates if BUFR layout is ambiguous.
    if len(lat) < 2 or len(lat) != len(lon):
        return []
    points = []
    for idx, (la, lo) in enumerate(zip(lat, lon)):
        if -90 <= la <= 90 and -180 <= lo <= 360:
            lo = ((lo + 180) % 360) - 180
            points.append({'lat': round(la, 3), 'lon': round(lo, 3), 'leadHours': idx * 6})
    if len(points) < 2:
        return []
    return [{'model': model_name, 'stormId': None, 'stormName': 'ECMWF tropical cyclone',
             'cycleTime': cycle.strftime('%Y-%m-%dT%H:%M:%SZ'),
             'source': 'https://www.ecmwf.int/en/forecasts/datasets/open-data', 'points': points}]


def main():
    now = datetime.now(timezone.utc)
    result = {'updatedAt': now.isoformat(), 'region': REGION, 'tracks': [], 'models': {}}
    cycles = candidates(now)
    with tempfile.TemporaryDirectory() as td:
        for name, cfg in MODELS.items():
            state = {'status': 'pending', 'message': '尚未取得可用產品'}
            for cycle in cycles:
                file = Path(td) / f'{name.replace(" ", "_")}_{cycle:%Y%m%d%H}.bufr'
                try:
                    step = download(cfg, cycle, file)
                    tracks = decode_bufr(file, name, cycle)
                    if tracks:
                        result['tracks'].extend(tracks)
                        state = {'status': 'available', 'message': f'解碼到路徑（step={step}h）',
                                 'cycleTime': cycle.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                 'source': 'ECMWF Open Data'}
                    else:
                        state = {'status': 'downloaded_unparsed',
                                 'message': '已下載 BUFR，但此解碼器尚未確認座標/時效欄位；未輸出路徑',
                                 'cycleTime': cycle.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                 'source': 'ECMWF Open Data'}
                    break
                except Exception as exc:
                    state = {'status': 'pending', 'message': str(exc)[:300]}
            result['models'][name] = state
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'寫入 {OUT}；路徑數={len(result["tracks"])}')

if __name__ == '__main__':
    main()

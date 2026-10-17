#!/usr/bin/env python3
"""Download official ECMWF tropical-cyclone track BUFR products.

This first-stage script never invents track coordinates. BUFR decoding is
conservative: tracks are emitted only when latitude/longitude arrays can be
matched unambiguously.
"""
import json
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path("data/typhoon-model-tracks.json")
REGION = {"west": 108, "south": 4, "east": 145, "north": 36}
MODELS = {
    "ECMWF IFS": {"model": "ifs", "stream": "oper", "step_00_12": 240, "step_06_18": 144},
    "ECMWF AIFS": {"model": "aifs-single", "stream": "oper", "step_00_12": 240, "step_06_18": 240},
    "AIFS ENS": {"model": "aifs-ens", "stream": "enfo", "step_00_12": 240, "step_06_18": 240},
}


def candidates(now):
    """Recent 00/06/12/18 UTC cycles, newest first."""
    cycle_hour = (now.hour // 6) * 6
    latest = now.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
    return [latest - timedelta(hours=back) for back in range(0, 48, 6)]


def download(cfg, cycle, target):
    from ecmwf.opendata import Client

    # Current ECMWF Open Data documentation: IFS TC tracks at 06/18 UTC
    # use stream=oper, type=tf, step up to 144h (scda/90h is legacy).
    step_key = "step_06_18" if cycle.hour in (6, 18) else "step_00_12"
    step = cfg[step_key]
    kwargs = {
        "date": cycle.strftime("%Y%m%d"),
        "time": cycle.hour,
        "stream": cfg["stream"],
        "type": "tf",
        "step": step,
        "target": str(target),
    }
    if cfg["model"] != "ifs":
        kwargs["model"] = cfg["model"]

    # Let the official client resolve the current product URL; do not construct URLs.
    Client(source="ecmwf").retrieve(**kwargs)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("ä¸è¼è«æ±å®æï¼ä½æªç¢çéç©º BUFR æªæ¡")
    return step


def decode_bufr(path, model_name, cycle):
    """Conservative initial decoder; no coordinates are inferred from missing fields."""
    raw = subprocess.check_output(["bufr_dump", "-j", str(path)], text=True)
    data = json.loads(raw)
    records = []

    def walk(obj):
        if isinstance(obj, dict):
            if "key" in obj and "value" in obj:
                records.append((str(obj["key"]).lower().split("#")[0], obj["value"]))
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    walk(value)

    walk(data)

    def nums(names):
        out = []
        for key, val in records:
            if key in names and isinstance(val, (int, float)):
                out.append(float(val))
        return out

    lat = nums({"latitude", "latitude of the center"})
    lon = nums({"longitude", "longitude of the center"})
    if len(lat) < 2 or len(lat) != len(lon):
        return []

    points = []
    for idx, (la, lo) in enumerate(zip(lat, lon)):
        if -90 <= la <= 90 and -180 <= lo <= 360:
            lo = ((lo + 180) % 360) - 180
            points.append({"lat": round(la, 3), "lon": round(lo, 3), "leadHours": idx * 6})
    if len(points) < 2:
        return []

    return [{
        "model": model_name,
        "stormId": None,
        "stormName": "ECMWF tropical cyclone",
        "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "https://www.ecmwf.int/en/forecasts/datasets/open-data",
        "points": points,
    }]


def main():
    now = datetime.now(timezone.utc)
    result = {"updatedAt": now.isoformat(), "region": REGION, "tracks": [], "models": {}}
    cycles = candidates(now)

    with tempfile.TemporaryDirectory() as td:
        for name, cfg in MODELS.items():
            state = {"status": "unavailable", "message": "æè¿èµ·å ±æéå°æªæ¾å°å¯ç¨çé¢±é¢¨è·¯å¾ç¢å"}
            for cycle in cycles:
                file_path = Path(td) / f"{name.replace(' ', '_')}_{cycle:%Y%m%d%H}.bufr"
                try:
                    step = download(cfg, cycle, file_path)
                    tracks = decode_bufr(file_path, name, cycle)
                    if tracks:
                        result["tracks"].extend(tracks)
                        state = {
                            "status": "available",
                            "message": f"å·²è§£æè·¯å¾ï¼step={step}hï¼",
                            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "source": "ECMWF Open Data",
                        }
                    else:
                        state = {
                            "status": "downloaded_unparsed",
                            "message": "å·²ä¸è¼ BUFRï¼ä½ç®åè§£ç¢¼å¨å°æªç¢ºèªåº§æ¨èæææ¬ä½ï¼æªè¼¸åºè·¯å¾",
                            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "source": "ECMWF Open Data",
                        }
                    break
                except Exception as exc:
                    state = {"status": "unavailable", "message": str(exc)[:300]}
            result["models"][name] = state

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"å¯«å¥ {OUT}ï¼è·¯å¾æ¸={len(result['tracks'])}")
    for model, state in result["models"].items():
        print(f"{model}: {state['status']} - {state['message']}")


if __name__ == "__main__":
    main()

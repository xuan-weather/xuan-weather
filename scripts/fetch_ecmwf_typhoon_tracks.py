#!/usr/bin/env python3
"""Fetch official ECMWF tropical-cyclone track BUFR products.

This script never synthesizes coordinates. It only publishes a track when the
BUFR decoder finds matching latitude/longitude values; otherwise it records a
clear status and diagnostic message in data/typhoon-model-tracks.json.
"""
import json
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path("data/typhoon-model-tracks.json")
REGION = {"west": 108, "south": 4, "east": 145, "north": 36}
MODELS = {
    "ECMWF IFS": {"model": "ifs", "stream": "oper"},
    "ECMWF AIFS": {"model": "aifs-single", "stream": "oper"},
    "AIFS ENS": {"model": "aifs-ens", "stream": "enfo"},
}


def candidate_cycles(now):
    """Try the current 6-hour cycle and the previous five cycles."""
    hour = (now.hour // 6) * 6
    latest = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return [latest - timedelta(hours=back) for back in range(0, 36, 6)]


def candidate_steps(model_name, cycle):
    # ECMWF Open Data TC-track products: IFS 00/12 cycles can extend to 360 h;
    # 06/18 cycles have a shorter availability window. Use 144 h for those runs.
    if model_name == "ECMWF IFS":
        return [144 if cycle.hour in (6, 18) else 240]
    return [240]


def download(cfg, model_name, cycle, target):
    from ecmwf.opendata import Client

    last_error = "No download attempt made"
    for step in candidate_steps(model_name, cycle):
        try:
            kwargs = {
                "date": cycle.strftime("%Y%m%d"),
                "time": cycle.hour,
                "stream": cfg["stream"],
                "type": "tf",
                "step": step,
                "target": str(target),
            }
            # IFS is the default model in ecmwf-opendata; explicit model is used
            # for the AI models.
            if cfg["model"] != "ifs":
                kwargs["model"] = cfg["model"]
            Client(source="aws").retrieve(**kwargs)
            if target.exists() and target.stat().st_size > 0:
                return step
            last_error = "Download command completed but produced an empty file"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(last_error)


def decode_bufr(path, model_name, cycle):
    """Decode cautiously; report actual ecCodes errors rather than hiding them."""
    proc = subprocess.run(
        ["bufr_dump", "-j", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "bufr_dump failed").strip()
        raise RuntimeError(f"bufr_dump failed (exit {proc.returncode}): {detail[:220]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bufr_dump output was not valid JSON: {exc}") from exc

    values = []

    def walk(obj):
        if isinstance(obj, dict):
            # ecCodes JSON layouts can vary; collect key/value pairs when present.
            if "key" in obj and "value" in obj:
                key = str(obj["key"]).lower().split("#", 1)[0]
                values.append((key, obj["value"]))
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    walk(value)

    walk(data)

    def numeric_values(keys):
        found = []
        for key, value in values:
            if key in keys and isinstance(value, (int, float)) and not isinstance(value, bool):
                found.append(float(value))
        return found

    latitudes = numeric_values({"latitude", "latitude of the center"})
    longitudes = numeric_values({"longitude", "longitude of the center"})
    if len(latitudes) < 2 or len(latitudes) != len(longitudes):
        return [], "BUFR downloaded, but matching latitude/longitude arrays were not found"

    points = []
    for index, (lat, lon) in enumerate(zip(latitudes, longitudes)):
        if not (-90 <= lat <= 90 and -360 <= lon <= 360):
            continue
        lon = ((lon + 180) % 360) - 180
        points.append({"lat": round(lat, 3), "lon": round(lon, 3), "leadHours": index * 6})

    if len(points) < 2:
        return [], "BUFR downloaded, but fewer than two valid coordinate pairs were decoded"

    track = {
        "model": model_name,
        "stormId": None,
        "stormName": "ECMWF tropical cyclone",
        "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "https://www.ecmwf.int/en/forecasts/datasets/open-data",
        "points": points,
    }
    return [track], "Decoded coordinate pairs from BUFR"


def main():
    now = datetime.now(timezone.utc)
    result = {
        "updatedAt": now.isoformat(),
        "region": REGION,
        "tracks": [],
        "models": {},
    }
    cycles = candidate_cycles(now)

    with tempfile.TemporaryDirectory() as temp_dir:
        for name, cfg in MODELS.items():
            state = {
                "status": "unavailable",
                "message": "No verified TC-track product found in recent cycles",
            }
            diagnostics = []
            for cycle in cycles:
                file_path = Path(temp_dir) / f"{name.replace(' ', '_')}_{cycle:%Y%m%d%H}.bufr"
                try:
                    step = download(cfg, name, cycle, file_path)
                    tracks, decode_message = decode_bufr(file_path, name, cycle)
                    if tracks:
                        result["tracks"].extend(tracks)
                        state = {
                            "status": "available",
                            "message": f"Decoded track product (step={step}h)",
                            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "source": "ECMWF Open Data",
                        }
                    else:
                        state = {
                            "status": "downloaded_unparsed",
                            "message": decode_message,
                            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "source": "ECMWF Open Data",
                        }
                    break
                except Exception as exc:
                    diagnostics.append(f"{cycle:%Y-%m-%d %HZ}: {str(exc)[:180]}")

            if state["status"] == "unavailable" and diagnostics:
                state["message"] = "No product retrieved from recent cycles. Last errors: " + " | ".join(diagnostics[-2:])
            result["models"][name] = state

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}; tracks={len(result['tracks'])}")
    for name, state in result["models"].items():
        print(f"{name}: {state['status']} â {state['message']}")


if __name__ == "__main__":
    main()

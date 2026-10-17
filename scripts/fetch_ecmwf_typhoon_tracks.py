#!/usr/bin/env python3
"""Fetch and parse official ECMWF tropical-cyclone track BUFR products.

Only storm-centre positions (meteorologicalAttributeSignificance == 1)
are published. Pressure-centre and wind-field coordinates are not joined
into the track. No synthetic coordinates are generated.
"""

import json
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path("data/typhoon-model-tracks.json")
REGION = {"west": 108, "south": 4, "east": 145, "north": 36}
# Keep a modest surrounding buffer so a track near the map edge is not lost.
SEARCH_REGION = {"west": 95, "south": -5, "east": 160, "north": 48}
MODELS = {
    "ECMWF IFS": {"model": "ifs", "stream": "oper"},
    "ECMWF AIFS": {"model": "aifs-single", "stream": "oper"},
    "AIFS ENS": {"model": "aifs-ens", "stream": "enfo"},
}


def candidate_cycles(now):
    hour = (now.hour // 6) * 6
    latest = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return [latest - timedelta(hours=back) for back in range(0, 36, 6)]


def candidate_steps(model_name, cycle):
    if model_name == "ECMWF IFS" and cycle.hour in (6, 18):
        return [144]
    return [240]


def download(cfg, model_name, cycle, target):
    from ecmwf.opendata import Client

    errors = []
    for step in candidate_steps(model_name, cycle):
        target.unlink(missing_ok=True)
        try:
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
            Client(source="aws").retrieve(**kwargs)
            if target.exists() and target.stat().st_size > 0:
                print(f"{model_name}: downloaded {target.stat().st_size} bytes; {cycle:%Y-%m-%d %HZ}, step={step}")
                return step
            errors.append(f"step={step}: empty output")
        except Exception as exc:
            errors.append(f"step={step}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def entries(node):
    """Yield all ecCodes {key,value} entries in a JSON subtree."""
    if isinstance(node, dict):
        if "key" in node:
            yield str(node["key"]), node.get("value")
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from entries(value)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                yield from entries(value)


def smallest_centre_nodes(node, found=None):
    """Find smallest subtrees containing exactly one storm-centre lat/lon pair."""
    if found is None:
        found = []
    if not isinstance(node, (dict, list)):
        return found

    pairs = list(entries(node))
    sigs = [v for k, v in pairs if k == "meteorologicalAttributeSignificance"]
    lats = [v for k, v in pairs if k == "latitude"]
    lons = [v for k, v in pairs if k == "longitude"]
    # A point node contains one significance code and one coordinate pair.
    # Ancestor nodes containing multiple forecast points are intentionally ignored.
    if sigs == [1] and len(lats) == 1 and len(lons) == 1:
        try:
            lat, lon = float(lats[0]), float(lons[0])
            if -90 <= lat <= 90 and -360 <= lon <= 360:
                found.append((lat, lon))
        except (TypeError, ValueError):
            pass
        return found

    if isinstance(node, dict):
        children = node.values()
    else:
        children = node
    for child in children:
        if isinstance(child, (dict, list)):
            smallest_centre_nodes(child, found)
    return found


def find_value(node, wanted):
    for key, value in entries(node):
        if key == wanted and value not in (None, ""):
            return value
    return None


def in_search_region(lat, lon):
    r = SEARCH_REGION
    return r["south"] <= lat <= r["north"] and r["west"] <= lon <= r["east"]


def decode_bufr(path, model_name, cycle):
    proc = subprocess.run(["bufr_dump", "-j", "s", str(path)], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "bufr_dump failed").strip()[:600])
    try:
        decoded = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bufr_dump returned invalid JSON: {exc}") from exc

    messages = decoded.get("messages", []) if isinstance(decoded, dict) else []
    tracks = []
    regional_storms = 0
    decoded_storms = 0
    for message in messages:
        storm_id = find_value(message, "stormIdentifier")
        storm_name = str(find_value(message, "longStormName") or storm_id or "Unnamed storm").strip()
        raw_points = smallest_centre_nodes(message)
        # Keep order from the BUFR sequence, remove only adjacent duplicate points.
        points = []
        for lat, lon in raw_points:
            lon = ((lon + 180) % 360) - 180
            point = {"lat": round(lat, 3), "lon": round(lon, 3), "leadHours": len(points) * 6}
            if not points or (point["lat"], point["lon"]) != (points[-1]["lat"], points[-1]["lon"]):
                points.append(point)
        if len(points) < 2:
            continue
        decoded_storms += 1
        if not any(in_search_region(p["lat"], p["lon"]) for p in points):
            continue
        regional_storms += 1
        tracks.append({
            "model": model_name,
            "stormId": str(storm_id) if storm_id else None,
            "stormName": storm_name,
            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "https://www.ecmwf.int/en/forecasts/datasets/open-data",
            "points": points,
        })

    return tracks, decoded_storms, regional_storms


def main():
    now = datetime.now(timezone.utc)
    result = {"updatedAt": now.isoformat(), "region": REGION, "tracks": [], "models": {}}
    cycles = candidate_cycles(now)

    with tempfile.TemporaryDirectory() as temp_dir:
        for name, cfg in MODELS.items():
            state = {"status": "unavailable", "message": "No verified track product found in recent cycles"}
            diagnostics = []
            for cycle in cycles:
                file_path = Path(temp_dir) / f"{name.replace(' ', '_')}_{cycle:%Y%m%d%H}.bufr"
                try:
                    step = download(cfg, name, cycle, file_path)
                    tracks, decoded_count, regional_count = decode_bufr(file_path, name, cycle)
                    result["tracks"].extend(tracks)
                    if regional_count:
                        state = {
                            "status": "available",
                            "message": f"Parsed {regional_count} regional storm track(s) from BUFR (step={step}h)",
                            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "source": "ECMWF Open Data",
                        }
                    else:
                        state = {
                            "status": "no_regional_tracks",
                            "message": f"BUFR decoded ({decoded_count} storm record(s)), but none intersect the western-Pacific display region",
                            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "source": "ECMWF Open Data",
                        }
                    break
                except Exception as exc:
                    diagnostics.append(f"{cycle:%Y-%m-%d %HZ}: {type(exc).__name__}: {exc}")
            if diagnostics and state["status"] == "unavailable":
                state["message"] = "No product retrieved from recent cycles. Last errors: " + " | ".join(diagnostics[-2:])
            result["models"][name] = state
            print(f"{name}: {state['status']} â {state['message']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}: {len(result['tracks'])} regional track(s)")


if __name__ == "__main__":
    main()

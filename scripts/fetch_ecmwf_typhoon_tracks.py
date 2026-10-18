#!/usr/bin/env python3
"""Fetch ECMWF tropical-cyclone BUFR tracks and publish verified centre points."""
import json
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path("data/typhoon-model-tracks.json")
REGION = {"west": 108, "south": 4, "east": 145, "north": 36}
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
    # ECMWF's official Open Data catalogue lists AIFS Single and AIFS ENS
    # tropical-cyclone BUFR tracks up to step 360 for all 00/06/12/18 UTC runs.
    # The previous step=240 request returned 404 for AIFS products. Try the
    # documented maximum first, then fall back to shorter steps only if needed.
    if model_name in ("ECMWF AIFS", "AIFS ENS"):
        return [360, 240, 144]
    # Keep the existing, working IFS behavior unchanged.
    if model_name == "ECMWF IFS" and cycle.hour in (6, 18):
        return [144]
    return [240]


def download(cfg, model_name, cycle, target):
    from ecmwf.opendata import Client
    errors = []
    for step in candidate_steps(model_name, cycle):
        target.unlink(missing_ok=True)
        try:
            args = {
                "date": cycle.strftime("%Y%m%d"), "time": cycle.hour,
                "stream": cfg["stream"], "type": "tf", "step": step,
                "target": str(target),
            }
            if cfg["model"] != "ifs":
                args["model"] = cfg["model"]
            Client(source="aws").retrieve(**args)
            if target.exists() and target.stat().st_size:
                print(f"{model_name}: downloaded {target.stat().st_size} bytes; {cycle:%Y-%m-%d %HZ}, step={step}")
                return step
            errors.append(f"step={step}: empty output")
        except Exception as exc:
            errors.append(f"step={step}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def entries(node):
    """Yield ecCodes key/value entries recursively."""
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


def find_value(node, wanted):
    for key, value in entries(node):
        if key == wanted and value not in (None, ""):
            return value
    return None


def centre_nodes(node, found=None):
    """Return smallest nodes carrying significance=1 and one centre coordinate pair."""
    if found is None:
        found = []
    if not isinstance(node, (dict, list)):
        return found
    pairs = list(entries(node))
    sigs = [v for k, v in pairs if k == "meteorologicalAttributeSignificance"]
    lats = [v for k, v in pairs if k == "latitude"]
    lons = [v for k, v in pairs if k == "longitude"]
    if sigs == [1] and len(lats) == 1 and len(lons) == 1:
        try:
            lat, lon = float(lats[0]), float(lons[0])
            if -90 <= lat <= 90 and -360 <= lon <= 360:
                found.append((lat, lon))
        except (TypeError, ValueError):
            pass
        return found
    children = node.values() if isinstance(node, dict) else node
    for child in children:
        if isinstance(child, (dict, list)):
            centre_nodes(child, found)
    return found


def time_periods(message):
    """Read forecast lead hours from BUFR timePeriod values in encoded order."""
    values = []
    for key, value in entries(message):
        if key == "timePeriod":
            try:
                n = int(value)
                if n >= 0:
                    values.append(n)
            except (TypeError, ValueError):
                continue
    return values


def in_search_region(lat, lon):
    r = SEARCH_REGION
    return r["south"] <= lat <= r["north"] and r["west"] <= lon <= r["east"]


def decode_bufr(path, model_name, cycle):
    proc = subprocess.run(["bufr_dump", "-j", "s", str(path)], text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "bufr_dump failed").strip()[:600])
    try:
        decoded = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bufr_dump returned invalid JSON: {exc}") from exc

    messages = decoded.get("messages", []) if isinstance(decoded, dict) else []
    tracks, decoded_count, regional_count = [], 0, 0
    for message in messages:
        storm_id = find_value(message, "stormIdentifier")
        raw_name = find_value(message, "longStormName")
        storm_name = str(raw_name or storm_id or "Unnamed storm").strip() or str(storm_id or "Unnamed storm")
        centres = centre_nodes(message)
        periods = time_periods(message)
        if len(centres) < 2:
            continue

        # In this BUFR product, the first centre is the analysis/initial point (lead 0);
        # following timePeriod entries describe subsequent forecast points. Never invent times.
        leads = [0] + periods
        count = min(len(centres), len(leads))
        points = []
        for (lat, lon), lead in zip(centres[:count], leads[:count]):
            lon = ((lon + 180) % 360) - 180
            point = {"lat": round(lat, 3), "lon": round(lon, 3), "leadHours": int(lead)}
            if points and point["leadHours"] <= points[-1]["leadHours"]:
                continue
            if not points or (point["lat"], point["lon"]) != (points[-1]["lat"], points[-1]["lon"]):
                points.append(point)
        if len(points) < 2:
            continue
        decoded_count += 1
        if not any(in_search_region(p["lat"], p["lon"]) for p in points):
            continue
        regional_count += 1
        tracks.append({
            "model": model_name, "stormId": str(storm_id) if storm_id else None,
            "stormName": storm_name, "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "https://www.ecmwf.int/en/forecasts/datasets/open-data", "points": points,
        })
    return tracks, decoded_count, regional_count


def same_path(a, b):
    """True when two tracks have a substantial near-identical overlap; avoid false duplicate lines."""
    pa, pb = a.get("points", []), b.get("points", [])
    if len(pa) < 4 or len(pb) < 4 or a.get("cycleTime") != b.get("cycleTime"):
        return False
    coords_a = {(round(p["lat"], 1), round(p["lon"], 1)) for p in pa}
    coords_b = {(round(p["lat"], 1), round(p["lon"], 1)) for p in pb}
    overlap = len(coords_a & coords_b)
    return overlap >= 4 and overlap / min(len(coords_a), len(coords_b)) >= 0.75


def deduplicate_tracks(tracks):
    result = []
    for track in tracks:
        duplicate_index = next((i for i, kept in enumerate(result)
                                if kept.get("model") == track.get("model") and same_path(kept, track)), None)
        if duplicate_index is None:
            result.append(track)
            continue
        old = result[duplicate_index]
        old_named = bool(old.get("stormName") and old.get("stormName") != old.get("stormId"))
        new_named = bool(track.get("stormName") and track.get("stormName") != track.get("stormId"))
        if (new_named and not old_named) or (new_named == old_named and len(track.get("points", [])) > len(old.get("points", []))):
            result[duplicate_index] = track
    return result


def main():
    now = datetime.now(timezone.utc)
    result = {"updatedAt": now.isoformat(), "region": REGION, "tracks": [], "models": {}}
    cycles = candidate_cycles(now)
    with tempfile.TemporaryDirectory() as temp_dir:
        for name, cfg in MODELS.items():
            state = {"status": "unavailable", "message": "No verified track product found in recent cycles"}
            errors = []
            for cycle in cycles:
                path = Path(temp_dir) / f"{name.replace(' ', '_')}_{cycle:%Y%m%d%H}.bufr"
                try:
                    step = download(cfg, name, cycle, path)
                    tracks, decoded_count, regional_count = decode_bufr(path, name, cycle)
                    result["tracks"].extend(tracks)
                    state = {
                        "status": "available" if regional_count else "no_regional_tracks",
                        "message": (f"Parsed {regional_count} regional storm track(s) from BUFR (step={step}h)"
                                    if regional_count else
                                    f"BUFR decoded ({decoded_count} storm record(s)), but none intersect the display region"),
                        "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "ECMWF Open Data",
                    }
                    break
                except Exception as exc:
                    errors.append(f"{cycle:%Y-%m-%d %HZ}: {type(exc).__name__}: {exc}")
            if errors and state["status"] == "unavailable":
                state["message"] = "No product retrieved from recent cycles. Last errors: " + " | ".join(errors[-2:])
            result["models"][name] = state
            print(f"{name}: {state['status']} — {state['message']}")

    result["tracks"] = deduplicate_tracks(result["tracks"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}: {len(result['tracks'])} regional track(s)")


if __name__ == "__main__":
    main()

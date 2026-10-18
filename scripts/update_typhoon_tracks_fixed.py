#!/usr/bin/env python3
"""Safely fetch official ECMWF IFS/AIFS tropical-cyclone BUFR tracks.

This is a conservative first-pass fetcher: it makes at most one request per
configured model for the latest eligible cycle, caps client retries, and never
replaces existing JSON unless at least one track is decoded and normalized.
BUFR descriptor mapping still needs to be verified against actual live products.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ecmwf.opendata import Client
from eccodes import (
    codes_bufr_new_from_file, codes_get, codes_get_array, codes_release,
    codes_set, codes_bufr_keys_iterator_new, codes_bufr_keys_iterator_next,
    codes_bufr_keys_iterator_get_name, codes_bufr_keys_iterator_delete,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "typhoon-model-tracks.json"
# Keep the first production test small: at most one request per model.
MODELS = [
    {"label": "ECMWF", "client_model": "ifs", "stream": "oper", "klass": None, "max_step": 240},
    {"label": "AIFS", "client_model": "aifs-single", "stream": "oper", "klass": "ai", "max_step": 360},
]


def safe_get(handle, key):
    try:
        return codes_get(handle, key)
    except Exception:
        return None


def safe_array(handle, key):
    try:
        values = codes_get_array(handle, key)
        return list(values) if values is not None else []
    except Exception:
        value = safe_get(handle, key)
        return [] if value is None else [value]


def keys_snapshot(handle):
    wanted = {
        "latitude", "longitude", "timePeriod", "forecastTime", "stormIdentifier",
        "stormName", "latitudeOfStormCentre", "longitudeOfStormCentre",
    }
    result = {}
    iterator = codes_bufr_keys_iterator_new(handle)
    try:
        while codes_bufr_keys_iterator_next(iterator):
            key = codes_bufr_keys_iterator_get_name(iterator)
            if key in wanted:
                values = safe_array(handle, key)
                if values:
                    result[key] = values
    finally:
        codes_bufr_keys_iterator_delete(iterator)
    return result


def first_value(values, index=None):
    if not values:
        return ""
    if index is not None and index < len(values) and values[index] not in (None, ""):
        return values[index]
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def decode_bufr(path: Path):
    """Decode common coordinate keys. Raises rather than inventing coordinates."""
    rows = []
    with path.open("rb") as stream:
        while True:
            handle = codes_bufr_new_from_file(stream)
            if handle is None:
                break
            try:
                codes_set(handle, "unpack", 1)
                data = keys_snapshot(handle)
                lats = data.get("latitude") or data.get("latitudeOfStormCentre") or []
                lons = data.get("longitude") or data.get("longitudeOfStormCentre") or []
                leads = data.get("timePeriod") or data.get("forecastTime") or []
                names = data.get("stormName") or []
                ids = data.get("stormIdentifier") or []
                for index in range(min(len(lats), len(lons))):
                    try:
                        lat, lon = float(lats[index]), float(lons[index])
                    except (TypeError, ValueError):
                        continue
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        continue
                    try:
                        lead = float(first_value(leads, index)) if leads else None
                    except (TypeError, ValueError):
                        lead = None
                    name = str(first_value(names, index)).strip()
                    storm_id = str(first_value(ids, index)).strip() or name
                    if lead is None or not storm_id:
                        # Do not publish points without an explicit lead and identity.
                        continue
                    rows.append({"lat": lat, "lon": lon, "leadHour": lead,
                                 "name": name, "stormId": storm_id})
            finally:
                codes_release(handle)
    unique, seen = [], set()
    for row in rows:
        key = (row["stormId"], row["leadHour"], round(row["lat"], 4), round(row["lon"], 4))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    if not unique:
        raise RuntimeError(
            f"{path.name}: no validated coordinate/lead/storm-id rows decoded. "
            "Existing JSON will be preserved; inspect actual BUFR descriptors."
        )
    return unique


def latest_eligible_cycle(now):
    """Choose the latest standard cycle at least 9 hours old for product availability."""
    cutoff = now - timedelta(hours=9)
    for day_offset in range(0, 3):
        day = (cutoff - timedelta(days=day_offset)).date()
        for hour in (18, 12, 6, 0):
            candidate = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if candidate <= cutoff:
                return candidate.strftime("%Y%m%d"), hour
    raise RuntimeError("Could not determine an eligible ECMWF cycle")


def retrieve_model(config, date, cycle, folder):
    label = config["label"]
    target = folder / f"{label.lower()}-{date}-{cycle:02d}.bufr"
    # ECMWF Open Data's documented track horizon is 240h at 00/12Z and 90h at 06/18Z.
    step = 90 if cycle in (6, 18) else config["max_step"]
    client = Client(
        source="ecmwf",
        model=config["client_model"],
        maximum_retries=2,
        retry_after=15,
        use_server_retry_after=True,
    )
    request = {
        "date": date,
        "time": cycle,
        "stream": config["stream"],
        "type": "tf",
        "step": step,
        "target": str(target),
    }
    if config["klass"]:
        request["class"] = config["klass"]
    try:
        client.retrieve(**request)
    except Exception as exc:
        return {"label": label, "ok": False,
                "error": f"Download failed for {date} {cycle:02d}Z (bounded retries): {exc}"}
    if not target.exists() or target.stat().st_size == 0:
        return {"label": label, "ok": False, "error": "Downloaded file is missing or empty."}
    try:
        points = decode_bufr(target)
    except Exception as exc:
        return {"label": label, "ok": False, "error": f"BUFR decode/validation failed: {exc}"}
    return {
        "label": label,
        "ok": True,
        "initTime": f"{date[:4]}-{date[4:6]}-{date[6:]}T{cycle:02d}:00:00Z",
        "points": points,
    }


def main():
    now = datetime.now(timezone.utc)
    date, cycle = latest_eligible_cycle(now)
    print(f"Testing one eligible ECMWF cycle only: {date} {cycle:02d}Z")
    results = []
    with tempfile.TemporaryDirectory() as temp_dir:
        folder = Path(temp_dir)
        for config in MODELS:
            result = retrieve_model(config, date, cycle, folder)
            results.append(result)
            print(f"{result['label']}: {'OK' if result.get('ok') else 'UNAVAILABLE'} — "
                  f"{result.get('error', str(len(result.get('points', []))) + ' decoded points')}")

    storms = {}
    for model_data in results:
        if not model_data.get("ok"):
            continue
        init = datetime.fromisoformat(model_data["initTime"].replace("Z", "+00:00"))
        grouped = {}
        for point in model_data["points"]:
            grouped.setdefault(point["stormId"], []).append(point)
        for storm_id, points in grouped.items():
            normalized = []
            for point in points:
                lead = point["leadHour"]
                normalized.append({
                    "lat": point["lat"], "lon": point["lon"],
                    "leadHour": int(lead),
                    "time": (init + timedelta(hours=lead)).isoformat().replace("+00:00", "Z"),
                })
            if not normalized:
                continue
            storm = storms.setdefault(storm_id, {
                "id": storm_id, "name": next((p["name"] for p in points if p["name"]), storm_id),
                "models": {},
            })
            storm["models"][model_data["label"]] = {
                "runs": [{"initTime": model_data["initTime"], "points": normalized}]
            }

    if not storms:
        print("No validated tracks produced; preserving existing JSON.", file=sys.stderr)
        print(json.dumps([{"model": r["label"], "error": r.get("error", "No usable tracks")}
                          for r in results], ensure_ascii=False, indent=2))
        sys.exit(2)

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "ECMWF Open Data",
        "sourceUrl": "https://www.ecmwf.int/en/forecasts/datasets/open-data",
        "storms": list(storms.values()),
        "modelStatus": [
            {"model": result["label"],
             "status": "ok" if result.get("ok") else "unavailable",
             "message": result.get("error", f"Decoded {len(result.get('points', []))} points")}
            for result in results
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote validated JSON with {len(output['storms'])} storm group(s) to {OUT}")


if __name__ == "__main__":
    main()

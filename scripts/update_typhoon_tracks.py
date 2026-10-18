#!/usr/bin/env python3
"""Fetch official ECMWF IFS/AIFS tropical-cyclone BUFR products and publish normalized JSON.

Important: ECMWF only publishes these products when a tropical cyclone is observed or forecast.
This script never creates example/stub storm tracks. If BUFR decoding cannot be mapped safely,
the workflow fails rather than publishing invented coordinates.
"""
from __future__ import annotations
import json, os, sys, tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ecmwf.opendata import Client
from eccodes import (
    codes_bufr_new_from_file, codes_get, codes_get_array, codes_get_size,
    codes_release, codes_set, codes_bufr_keys_iterator_new,
    codes_bufr_keys_iterator_next, codes_bufr_keys_iterator_get_name,
    codes_bufr_keys_iterator_delete,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "typhoon-model-tracks.json"
MODELS = [
    # Display label, ECMWF Open Data model argument, stream, class, maximum track step
    ("ECMWF", None, "oper", None, 240),
    ("AIFS", "aifs-single", "oper", "ai", 360),
]
MISSING = {"latitude", "longitude", "lat", "lon"}

def safe_get(handle, key):
    try:
        return codes_get(handle, key)
    except Exception:
        return None

def safe_array(handle, key):
    try:
        vals = codes_get_array(handle, key)
        return list(vals) if vals is not None else []
    except Exception:
        value = safe_get(handle, key)
        return [] if value is None else [value]

def keys_snapshot(handle):
    """Read common BUFR keys without assuming every ECMWF cycle uses identical descriptors."""
    out = {}
    it = codes_bufr_keys_iterator_new(handle)
    try:
        while codes_bufr_keys_iterator_next(it):
            key = codes_bufr_keys_iterator_get_name(it)
            if key in {
                "latitude", "longitude", "timePeriod", "forecastTime",
                "stormIdentifier", "stormName", "year", "month", "day",
                "hour", "minute", "typicalYear", "typicalMonth",
                "typicalDay", "typicalHour", "typicalMinute",
                "latitudeOfStormCentre", "longitudeOfStormCentre",
                "timeSignificance", "significanceOfReferenceTime",
            }:
                vals = safe_array(handle, key)
                if vals:
                    out[key] = vals
    finally:
        codes_bufr_keys_iterator_delete(it)
    return out

def first_nonempty(*values, default=None):
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if item not in (None, "", 0, "0"):
                    return item
        elif value not in ("", 0, "0"):
            return value
    return default

def decode_bufr(path):
    """Decode coordinate rows from BUFR. Raises if no usable coordinate pairs are found."""
    rows = []
    with open(path, "rb") as stream:
        while True:
            handle = codes_bufr_new_from_file(stream)
            if handle is None:
                break
            try:
                codes_set(handle, "unpack", 1)
                data = keys_snapshot(handle)
                lats = data.get("latitude") or data.get("latitudeOfStormCentre") or []
                lons = data.get("longitude") or data.get("longitudeOfStormCentre") or []
                if not lats or not lons:
                    continue
                n = min(len(lats), len(lons))
                leads = data.get("timePeriod") or data.get("forecastTime") or []
                names = data.get("stormName") or []
                ids = data.get("stormIdentifier") or []
                for i in range(n):
                    try:
                        lat, lon = float(lats[i]), float(lons[i])
                    except (TypeError, ValueError):
                        continue
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        continue
                    lead = None
                    if leads:
                        try:
                            lead = float(leads[min(i, len(leads)-1)])
                        except (TypeError, ValueError):
                            pass
                    name = str(first_nonempty(names[i:i+1], names, default=None) or "").strip()
                    storm_id = str(first_nonempty(ids[i:i+1], ids, default=None) or name or "UNIDENTIFIED").strip()
                    rows.append({
                        "lat": lat, "lon": lon, "leadHour": lead,
                        "name": name, "stormId": storm_id,
                    })
            finally:
                codes_release(handle)
    # Remove exact duplicate coordinate/lead rows while preserving order.
    unique, seen = [], set()
    for row in rows:
        key = (row["stormId"], row["leadHour"], round(row["lat"], 4), round(row["lon"], 4))
        if key not in seen:
            seen.add(key); unique.append(row)
    if not unique:
        raise RuntimeError(
            f"{path.name}: BUFR was downloaded but no latitude/longitude rows could be decoded. "
            "Do not publish this as an empty storm season; inspect ecCodes key mapping first."
        )
    return unique

def retrieve_model(label, model, stream, klass, max_step, date, cycle, folder):
    client = Client(source="ecmwf")
    target = folder / f"{label.lower()}-{date}-{cycle:02d}.bufr"
    # ECMWF IFS track products have shorter horizons for 06Z/18Z cycles.
    request_step = min(max_step, 144) if label == "ECMWF" and cycle in (6, 18) else max_step
    args = dict(date=date, time=cycle, stream=stream, type="tf",
                step=request_step, target=str(target))
    if model:
        args["model"] = model
    if klass:
        args["class"] = klass
    try:
        client.retrieve(**args)
    except Exception as exc:
        return {"label": label, "ok": False, "error": f"Official product not available for {date} {cycle:02d}Z: {exc}"}
    if not target.exists() or target.stat().st_size == 0:
        return {"label": label, "ok": False, "error": "Official product file was empty or missing."}
    points = decode_bufr(target)
    return {"label": label, "ok": True, "initTime": f"{date[:4]}-{date[4:6]}-{date[6:]}T{cycle:02d}:00:00Z", "points": points}

def main():
    now = datetime.now(timezone.utc)
    # Try the most recent four standard cycles, newest first; stop at the first cycle
    # where at least one official model returns decodable data.
    cycles = []
    for back in range(0, 3):
        day = (now - timedelta(days=back)).date()
        for hour in (18, 12, 6, 0):
            cycle_dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if cycle_dt <= now - timedelta(hours=3):
                cycles.append((day.strftime("%Y%m%d"), hour))
    result = None
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        for date, cycle in cycles:
            model_results = []
            for label, model, stream, klass, max_step in MODELS:
                try:
                    model_results.append(retrieve_model(label, model, stream, klass, max_step, date, cycle, folder))
                except Exception as exc:
                    model_results.append({"label": label, "ok": False, "error": str(exc)})
            good = [m for m in model_results if m.get("ok")]
            if good:
                # Group points per model. Since product BUFR encodings can differ by cycle,
                # preserve source rows and only group when the storm identifier is explicit.
                storms = {}
                for model_data in good:
                    grouped = {}
                    for p in model_data["points"]:
                        key = p["stormId"] or p["name"] or "UNIDENTIFIED"
                        grouped.setdefault(key, []).append(p)
                    for key, points in grouped.items():
                        # Only use points with an explicit lead hour. No inferred timing.
                        normalized = []
                        for p in points:
                            if p["leadHour"] is None:
                                continue
                            normalized.append({
                                "lat": p["lat"], "lon": p["lon"],
                                "leadHour": int(p["leadHour"]),
                                "time": (datetime.fromisoformat(model_data["initTime"].replace("Z","+00:00")) +
                                         timedelta(hours=float(p["leadHour"]))).isoformat().replace("+00:00","Z")
                            })
                        if not normalized:
                            continue
                        storm = storms.setdefault(key, {"id": key, "name": points[0]["name"] or key, "models": {}})
                        storm["models"][model_data["label"]] = {
                            "runs": [{"initTime": model_data["initTime"], "points": normalized}]
                        }
                if storms:
                    result = {
                        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
                        "source": "ECMWF Open Data",
                        "sourceUrl": "https://www.ecmwf.int/en/forecasts/datasets/open-data",
                        "storms": list(storms.values()),
                        "modelStatus": [
                            {"model": m["label"], "status": "ok" if m.get("ok") else "unavailable",
                             "message": "Official BUFR decoded" if m.get("ok") else m.get("error","")}
                            for m in model_results
                        ],
                    }
                    break
        if result is None:
            # No valid tracks can mean no active/forecast cyclone, but can also mean format changes.
            # Preserve the previous file instead of overwriting good data with a misleading empty list.
            print("No validated track points were produced. Existing JSON will be preserved.", file=sys.stderr)
            sys.exit(2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Published {len(result['storms'])} storm groups to {OUT}")
    for status in result["modelStatus"]:
        print(status)

if __name__ == "__main__":
    main()

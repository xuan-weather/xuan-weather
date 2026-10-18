
#!/usr/bin/env python3
"""Fetch official ECMWF tropical-cyclone BUFR tracks and preserve published tracks."""

import json
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path("data/typhoon-model-tracks.json")

REGION = {
    "west": 108,
    "south": 4,
    "east": 145,
    "north": 36,
}

SEARCH_REGION = {
    "west": 95,
    "south": -5,
    "east": 160,
    "north": 48,
}

MODELS = {
    "ECMWF IFS": {"model": "ifs", "stream": "oper"},
    "ECMWF AIFS": {"model": "aifs-single", "stream": "oper"},
    "AIFS ENS": {"model": "aifs-ens", "stream": "enfo"},
}


def candidate_cycles(now):
    """Try the latest six-hour cycle and recent previous cycles."""
    hour = (now.hour // 6) * 6
    latest = now.replace(
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    return [
        latest - timedelta(hours=back)
        for back in range(0, 36, 6)
    ]


def candidate_steps(model_name, cycle):
    """Choose forecast steps without changing the existing IFS settings."""
    if model_name in ("ECMWF AIFS", "AIFS ENS"):
        return [360, 240, 144]

    if model_name == "ECMWF IFS" and cycle.hour in (6, 18):
        return [144]

    return [240]


def is_rate_limit_error(exc):
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "503",
            "429",
            "slow down",
            "too many requests",
            "rate limit",
        )
    )


def download(cfg, model_name, cycle, target):
    """Download a real ECMWF Open Data BUFR product."""
    from ecmwf.opendata import Client

    errors = []

    for step in candidate_steps(model_name, cycle):
        target.unlink(missing_ok=True)

        args = {
            "date": cycle.strftime("%Y%m%d"),
            "time": cycle.hour,
            "stream": cfg["stream"],
            "type": "tf",
            "step": step,
            "target": str(target),
        }

        if cfg["model"] != "ifs":
            args["model"] = cfg["model"]

        try:
            # Keep retries finite so a throttled request cannot wait indefinitely.
            client = Client(
                source="aws",
                maximum_retries=2,
                retry_after=(10, 20, 2),
                use_server_retry_after=False,
            )
            client.retrieve(**args)

            if target.exists() and target.stat().st_size > 0:
                print(
                    f"{model_name}: downloaded "
                    f"{target.stat().st_size} bytes; "
                    f"{cycle:%Y-%m-%d %HZ}, step={step}"
                )
                return step

            errors.append(f"step={step}: empty output")

        except Exception as exc:
            message = (
                f"step={step}: {type(exc).__name__}: {exc}"
            )
            errors.append(message)
            target.unlink(missing_ok=True)

            # Do not keep requesting other steps after a rate-limit response.
            if is_rate_limit_error(exc):
                raise RuntimeError(message) from exc

            # Other errors, including 404, can try the next candidate step.
            print(f"{model_name}: {message}")

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
    """Find centre coordinates marked with meteorological significance 1."""
    if found is None:
        found = []

    if not isinstance(node, (dict, list)):
        return found

    pairs = list(entries(node))
    sigs = [
        value for key, value in pairs
        if key == "meteorologicalAttributeSignificance"
    ]
    lats = [
        value for key, value in pairs
        if key == "latitude"
    ]
    lons = [
        value for key, value in pairs
        if key == "longitude"
    ]

    if sigs == [1] and len(lats) == 1 and len(lons) == 1:
        try:
            lat = float(lats[0])
            lon = float(lons[0])

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
    """Read non-negative forecast timePeriod values."""
    values = []

    for key, value in entries(message):
        if key == "timePeriod":
            try:
                number = int(value)
                if number >= 0:
                    values.append(number)
            except (TypeError, ValueError):
                continue

    return values


def in_search_region(lat, lon):
    region = SEARCH_REGION
    return (
        region["south"] <= lat <= region["north"]
        and region["west"] <= lon <= region["east"]
    )


def decode_bufr(path, model_name, cycle):
    """Decode real BUFR data with ecCodes; do not invent track points."""
    proc = subprocess.run(
        ["bufr_dump", "-j", "s", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )

    if proc.returncode:
        raise RuntimeError(
            (proc.stderr or proc.stdout or "bufr_dump failed").strip()[:600]
        )

    try:
        decoded = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"bufr_dump returned invalid JSON: {exc}"
        ) from exc

    messages = (
        decoded.get("messages", [])
        if isinstance(decoded, dict)
        else []
    )

    tracks = []
    decoded_count = 0
    regional_count = 0

    for message in messages:
        storm_id = find_value(message, "stormIdentifier")
        raw_name = find_value(message, "longStormName")

        storm_name = str(
            raw_name or storm_id or "Unnamed storm"
        ).strip()

        centres = centre_nodes(message)
        periods = time_periods(message)

        if len(centres) < 2:
            continue

        # The first centre is the initial point; later leads come from BUFR.
        leads = [0] + periods
        count = min(len(centres), len(leads))
        points = []

        for (lat, lon), lead in zip(centres[:count], leads[:count]):
            lon = ((lon + 180) % 360) - 180

            point = {
                "lat": round(lat, 3),
                "lon": round(lon, 3),
                "leadHours": int(lead),
            }

            if points and point["leadHours"] <= points[-1]["leadHours"]:
                continue

            if (
                not points
                or (point["lat"], point["lon"])
                != (points[-1]["lat"], points[-1]["lon"])
            ):
                points.append(point)

        if len(points) < 2:
            continue

        decoded_count += 1

        if not any(
            in_search_region(point["lat"], point["lon"])
            for point in points
        ):
            continue

        regional_count += 1

        tracks.append({
            "model": model_name,
            "stormId": str(storm_id) if storm_id else None,
            "stormName": storm_name,
            "cycleTime": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": (
                "https://www.ecmwf.int/en/forecasts/datasets/open-data"
            ),
            "points": points,
        })

    return tracks, decoded_count, regional_count


def same_path(track_a, track_b):
    """Detect near-identical tracks from the same model and cycle."""
    points_a = track_a.get("points", [])
    points_b = track_b.get("points", [])

    if len(points_a) < 4 or len(points_b) < 4:
        return False

    if track_a.get("cycleTime") != track_b.get("cycleTime"):
        return False

    coords_a = {
        (round(point["lat"], 1), round(point["lon"], 1))
        for point in points_a
    }
    coords_b = {
        (round(point["lat"], 1), round(point["lon"], 1))
        for point in points_b
    }

    overlap = len(coords_a & coords_b)

    return (
        overlap >= 4
        and overlap / min(len(coords_a), len(coords_b)) >= 0.75
    )


def deduplicate_tracks(tracks):
    """Remove duplicate lines without removing unrelated model tracks."""
    result = []

    for track in tracks:
        duplicate_index = next(
            (
                index
                for index, kept in enumerate(result)
                if kept.get("model") == track.get("model")
                and same_path(kept, track)
            ),
            None,
        )

        if duplicate_index is None:
            result.append(track)
            continue

        old = result[duplicate_index]

        old_named = bool(
            old.get("stormName")
            and old.get("stormName") != old.get("stormId")
        )
        new_named = bool(
            track.get("stormName")
            and track.get("stormName") != track.get("stormId")
        )

        if (
            (new_named and not old_named)
            or (
                new_named == old_named
                and len(track.get("points", []))
                > len(old.get("points", []))
            )
        ):
            result[duplicate_index] = track

    return result


def load_existing_data():
    """Load the currently published JSON so existing IFS tracks are retained."""
    if not OUT.exists():
        return {"tracks": [], "models": {}}

    try:
        data = json.loads(OUT.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            raise ValueError("Existing JSON is not an object")

        if not isinstance(data.get("tracks"), list):
            data["tracks"] = []

        if not isinstance(data.get("models"), dict):
            data["models"] = {}

        return data

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # Do not silently overwrite a broken existing file.
        raise RuntimeError(
            f"Cannot safely read existing {OUT}: {exc}"
        ) from exc


def main():
    now = datetime.now(timezone.utc)
    previous = load_existing_data()

    # Start with the published tracks; new real tracks are merged later.
    result = {
        "updatedAt": now.isoformat(),
        "region": previous.get("region", REGION),
        "tracks": list(previous.get("tracks", [])),
        "models": dict(previous.get("models", {})),
    }

    cycles = candidate_cycles(now)

    with tempfile.TemporaryDirectory() as temp_dir:
        for name, cfg in MODELS.items():
            previous_state = result["models"].get(name, {})
            previous_track_count = sum(
                1 for track in result["tracks"]
                if track.get("model") == name
            )

            state = {
                "status": "unavailable",
                "message": "No verified track product found in recent cycles",
            }
            errors = []
            fetched = False

            for cycle in cycles:
                path = (
                    Path(temp_dir)
                    / f"{name.replace(' ', '_')}_{cycle:%Y%m%d%H}.bufr"
                )

                try:
                    step = download(cfg, name, cycle, path)

                    tracks, decoded_count, regional_count = decode_bufr(
                        path, name, cycle
                    )

                    result["tracks"].extend(tracks)

                    state = {
                        "status": (
                            "available"
                            if regional_count
                            else "no_regional_tracks"
                        ),
                        "message": (
                            f"Parsed {regional_count} regional storm "
                            f"track(s) from BUFR (step={step}h)"
                            if regional_count
                            else (
                                f"BUFR decoded ({decoded_count} storm "
                                "record(s)), but none intersect the "
                                "display region"
                            )
                        ),
                        "cycleTime": cycle.strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        "source": "ECMWF Open Data",
                    }

                    fetched = True
                    break

                except Exception as exc:
                    message = (
                        f"{cycle:%Y-%m-%d %HZ}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    errors.append(message)
                    print(f"{name}: {message}")

                                if is_rate_limit_error(exc):
                import time
                print(f"{name}: 伺服器限流，等待 15 秒後繼續嘗試較早時次")
                time.sleep(15)
                continue

            if not fetched:
                if previous_track_count > 0:
                    # Keep the old model status and tracks if refresh fails.
                    state = dict(previous_state)
                    state["message"] = (
                        "本次更新失敗，保留先前已發布路徑。"
                        + (
                            " 最近錯誤：" + " | ".join(errors[-2:])
                            if errors
                            else ""
                        )
                    )
                else:
                    state["status"] = "unavailable"
                    if errors:
                        state["message"] = (
                            "No product retrieved from recent cycles. "
                            "Last errors: " + " | ".join(errors[-2:])
                        )

            result["models"][name] = state
            print(f"{name}: {state['status']} — {state['message']}")

    # Preserve existing tracks and add only tracks decoded from official BUFR.
    result["tracks"] = deduplicate_tracks(result["tracks"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote {OUT}: "
        f"{len(result['tracks'])} regional track(s)"
    )


if __name__ == "__main__":
    main()

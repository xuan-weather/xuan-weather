
#!/usr/bin/env python3
"""Fetch official ECMWF tropical-cyclone track BUFR products.

Only publish coordinates decoded from downloaded ECMWF BUFR files.
Never synthesize forecast tracks.
"""

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

MODELS = {
    "ECMWF IFS": {
        "model": "ifs",
        "stream": "oper",
    },
    "ECMWF AIFS": {
        "model": "aifs-single",
        "stream": "oper",
    },
    "AIFS ENS": {
        "model": "aifs-ens",
        "stream": "enfo",
    },
}


def candidate_cycles(now):
    """Try the latest six-hour cycle and previous cycles."""
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
    """Choose forecast steps for the requested model and cycle."""
    if model_name == "ECMWF IFS":
        if cycle.hour in (6, 18):
            return [144]
        return [240]

    return [240]


def download(cfg, model_name, cycle, target):
    """Download one official ECMWF TC-track BUFR product."""
    from ecmwf.opendata import Client

    errors = []

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

            if cfg["model"] != "ifs":
                kwargs["model"] = cfg["model"]

            client = Client(source="aws")
            client.retrieve(**kwargs)

            if target.exists() and target.stat().st_size > 0:
                print(
                    f"{model_name}: downloaded "
                    f"{target.stat().st_size} bytes "
                    f"for {cycle:%Y-%m-%d %HZ}, step={step}"
                )
                return step

            errors.append(
                f"step={step}: download produced an empty file"
            )

        except Exception as exc:
            errors.append(
                f"step={step}: {type(exc).__name__}: {exc}"
            )

    raise RuntimeError("; ".join(errors))


def decode_bufr(path, model_name, cycle):
    """Decode BUFR using ecCodes JSON mode."""
    proc = subprocess.run(
        ["bufr_dump", "-j", "s", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )

    if proc.returncode != 0:
        detail = (
            proc.stderr
            or proc.stdout
            or "bufr_dump returned no diagnostic"
        ).strip()

        raise RuntimeError(
            f"bufr_dump failed (exit {proc.returncode}): "
            f"{detail[:500]}"
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"bufr_dump output is not valid JSON: {exc}; "
            f"output prefix={proc.stdout[:300]!r}"
        ) from exc

    values = []

    def walk(obj):
        if isinstance(obj, dict):
            key = obj.get("key")
            if key is not None and "value" in obj:
                values.append(
                    (str(key).lower(), obj["value"])
                )

            for child in obj.values():
                if isinstance(child, (dict, list)):
                    walk(child)

        elif isinstance(obj, list):
            for child in obj:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(data)

    def collect(keys):
        found = []

        for key, value in values:
            normalized = key.replace("_", "").replace(" ", "")

            if normalized in keys:
                if isinstance(value, (int, float)):
                    if not isinstance(value, bool):
                        found.append(float(value))

        return found

    latitudes = collect({
        "latitude",
        "latitudeofcenter",
        "latitudeofstormcenter",
    })

    longitudes = collect({
        "longitude",
        "longitudeofcenter",
        "longitudeofstormcenter",
    })

    if len(latitudes) < 2 or len(latitudes) != len(longitudes):
        return [], (
            "BUFR downloaded, but matching latitude/longitude "
            f"arrays were not found. "
            f"Decoded latitude values={len(latitudes)}, "
            f"longitude values={len(longitudes)}. "
            "Inspect bufr_dump JSON structure before mapping."
        )

    points = []

    for index, (lat, lon) in enumerate(
        zip(latitudes, longitudes)
    ):
        if not (-90 <= lat <= 90):
            continue

        if not (-360 <= lon <= 360):
            continue

        lon = ((lon + 180) % 360) - 180

        points.append({
            "lat": round(lat, 3),
            "lon": round(lon, 3),
            "leadHours": index * 6,
        })

    if len(points) < 2:
        return [], (
            "BUFR was downloaded, but fewer than two "
            "valid coordinate pairs were decoded."
        )

    track = {
        "model": model_name,
        "stormId": None,
        "stormName": "ECMWF tropical cyclone",
        "cycleTime": cycle.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "source": (
            "https://www.ecmwf.int/en/forecasts/"
            "datasets/open-data"
        ),
        "points": points,
    }

    return [track], (
        f"Decoded {len(points)} coordinate pairs "
        "from BUFR."
    )


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
                "message": (
                    "No verified TC-track product "
                    "retrieved from recent cycles."
                ),
            }

            diagnostics = []

            for cycle in cycles:
                filename = (
                    f"{name.replace(' ', '_')}_"
                    f"{cycle:%Y%m%d%H}.bufr"
                )
                file_path = Path(temp_dir) / filename

                try:
                    step = download(
                        cfg,
                        name,
                        cycle,
                        file_path,
                    )

                    tracks, decode_message = decode_bufr(
                        file_path,
                        name,
                        cycle,
                    )

                    if tracks:
                        result["tracks"].extend(tracks)
                        state = {
                            "status": "available",
                            "message": (
                                f"{decode_message} "
                                f"(step={step}h)"
                            ),
                            "cycleTime": cycle.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "source": "ECMWF Open Data",
                        }
                    else:
                        state = {
                            "status": "downloaded_unparsed",
                            "message": decode_message,
                            "cycleTime": cycle.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "source": "ECMWF Open Data",
                        }

                    # A product was downloaded; do not disguise a
                    # decode failure as a missing product.
                    break

                except Exception as exc:
                    diagnostics.append(
                        f"{cycle:%Y-%m-%d %HZ}: "
                        f"{type(exc).__name__}: {str(exc)[:300]}"
                    )

            if state["status"] == "unavailable" and diagnostics:
                state["message"] = (
                    "No product retrieved from recent cycles. "
                    "Last errors: "
                    + " | ".join(diagnostics[-3:])
                )

            result["models"][name] = state
            print(
                f"{name}: {state['status']} - "
                f"{state['message']}"
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote {OUT}; tracks={len(result['tracks'])}"
    )


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""Fetch official NOAA ATCF tropical-cyclone tracks without replacing ECMWF data."""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from html.parser import HTMLParser

OUT = Path("data/typhoon-model-tracks.json")
BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/ens_tracker/v1.3"

MODELS = {
    "gfs": "GFS",
    "gefs": "GEFS",
    "aigfs": "AIGFS",
    "aigefs": "AIGEFS",
}

REGION = {"west": 108, "south": 4, "east": 145, "north": 36}
USER_AGENT = "XuanWeather/1.0 (official NOAA ATCF track downloader)"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href", "")
            if href:
                self.hrefs.append(href)


def read_url(url, timeout=20):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def list_links(url):
    try:
        parser = Links()
        parser.feed(read_url(url))
        return parser.hrefs
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"Unavailable: {url} ({exc})")
        return []


def parse_coord(value, is_lat):
    value = value.strip().upper()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([NSEW])", value)
    if not match:
        try:
            number = float(value)
            return number if -90 <= number <= 360 else None
        except ValueError:
            return None

    number = float(match.group(1)) / 10
    direction = match.group(2)
    if direction in ("S", "W"):
        number = -number

    limit = 90 if is_lat else 360
    return number if abs(number) <= limit else None


def parse_atcf(text):
    grouped = {}

    for line in text.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) < 8:
            continue

        try:
            basin = fields[0].upper()
            number = fields[1].zfill(2)
            cycle = fields[2]
            lead = int(fields[5])
            lat = parse_coord(fields[6], True)
            lon = parse_coord(fields[7], False)
        except (ValueError, IndexError):
            continue

        if lat is None or lon is None:
            continue
        if not re.fullmatch(r"\d{10}", cycle):
            continue

        storm_id = f"{basin}{number}"
        key = (storm_id, cycle)

        name = ""
        if len(fields) > 27:
            name = fields[27].strip().upper()
        if not name:
            name = storm_id

        grouped.setdefault(key, {"stormName": name, "points": []})
        grouped[key]["points"].append({
            "lat": round(lat, 2),
            "lon": round(lon, 2),
            "leadHours": lead,
        })

    return grouped


def inside_region(points):
    r = REGION
    return any(
        r["south"] <= p["lat"] <= r["north"]
        and r["west"] <= p["lon"] <= r["east"]
        for p in points
    )


def main():
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Cannot safely read existing JSON: {exc}")
    else:
        data = {"region": REGION, "tracks": []}

    if not isinstance(data, dict) or not isinstance(data.get("tracks"), list):
        raise SystemExit("Existing JSON has an unexpected structure; stopping.")

    # Replace only NOAA tracks produced by this script.
    kept = [
        track for track in data["tracks"]
        if track.get("model") not in {"GFS", "GEFS", "AIGFS", "AIGEFS"}
    ]

    now = datetime.now(timezone.utc)
    dates = [(now - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
    new_tracks = []

    for folder, model_name in MODELS.items():
        for date in dates:
            for cycle_hour in ("00", "06", "12", "18"):
                cycle = f"{date}{cycle_hour}"
                directory = (
                    f"{BASE}/{folder}.{date}/{cycle_hour}/tctrack/"
                )

                names = list_links(directory)
                files = sorted({
                    name for name in names
                    if name.endswith(".cyclone.trackatcfunix")
                    and "/" not in name
                })

                # Avoid excessive downloads from a single directory.
                if folder in {"gefs", "aigefs"}:
                    files = files[:40]
                else:
                    files = files[:12]

                for filename in files:
                    url = directory + filename
                    try:
                        raw = read_url(url)
                        parsed = parse_atcf(raw)
                    except (HTTPError, URLError, TimeoutError, OSError) as exc:
                        print(f"Download failed: {url} ({exc})")
                        continue

                    for (storm_id, cycle_stamp), item in parsed.items():
                        points = sorted(
                            item["points"],
                            key=lambda point: point["leadHours"],
                        )
                        # Keep only one point per forecast hour.
                        unique = {}
                        for point in points:
                            unique[point["leadHours"]] = point
                        points = list(unique.values())

                        if not points or not inside_region(points):
                            continue

                        try:
                            cycle_time = datetime.strptime(
                                cycle_stamp, "%Y%m%d%H"
                            ).replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue

                        # Include the filename/member in the identity so
                        # ensemble members do not overwrite one another.
                        new_tracks.append({
                            "model": model_name,
                            "stormId": storm_id,
                            "stormName": item["stormName"],
                            "cycleTime": cycle_time.isoformat().replace(
                                "+00:00", "Z"
                            ),
                            "source": url,
                            "member": filename.split(".")[0],
                            "points": points,
                        })

                    time.sleep(0.15)

    # Keep old data if NOAA returned no valid tracks.
    if not new_tracks:
        print("No valid NOAA tracks found; existing JSON is unchanged.")
        return

    data["updatedAt"] = now.isoformat()
    data.setdefault("region", REGION)
    data["tracks"] = kept + new_tracks

    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(OUT)

    counts = {}
    for track in new_tracks:
        counts[track["model"]] = counts.get(track["model"], 0) + 1

    print("NOAA tracks added:", counts)
    print("Total tracks:", len(data["tracks"]))
    print("Updated:", OUT)


if __name__ == "__main__":
    main()

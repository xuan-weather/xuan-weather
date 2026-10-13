#!/usr/bin/env python3
"""Fetch official CWA automatic rainfall observations without exposing the API key."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_KEY = os.environ.get("CWA_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: GitHub Actions secret CWA_API_KEY is missing.", file=sys.stderr)
    sys.exit(2)

endpoint = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"
url = endpoint + "?" + urllib.parse.urlencode({"Authorization": API_KEY, "format": "JSON"})
request = urllib.request.Request(url, headers={"User-Agent": "XuanWeather-CWA-Rainfall/1.0", "Accept": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8-sig"))
except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    print(f"ERROR: CWA request/JSON parsing failed: {type(exc).__name__}", file=sys.stderr)
    sys.exit(1)

if not isinstance(payload, dict) or payload.get("success") == "false" or payload.get("error"):
    print("ERROR: CWA returned an API error; response content withheld from logs.", file=sys.stderr)
    sys.exit(1)
records = payload.get("records") or {}
stations = records.get("Station") or records.get("station") or records.get("Location") or records.get("location")
if isinstance(stations, dict):
    stations = [stations]
if not isinstance(stations, list) or not stations:
    print("ERROR: CWA response contains no station records; refusing to publish empty data.", file=sys.stderr)
    sys.exit(1)

output = {
    "source": "CWA Open Data O-A0002-001",
    "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "recordCount": len(stations),
    "data": payload,
}
path = Path("data/rainfall-latest.json")
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
tmp.replace(path)
print(f"Published {len(stations)} official CWA rainfall station records to {path}.")

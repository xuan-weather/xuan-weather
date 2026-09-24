import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ.get("CWA_API_KEY", "").strip()
OUTPUT = "data/typhoon.json"

if not API_KEY:
    raise SystemExit("CWA_API_KEY GitHub Actions Secret is not set.")

url = (
    "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/"
    "W-C0034-005"
    "?Authorization="
    + urllib.parse.quote(API_KEY, safe="")
    + "&format=JSON"
)

request = urllib.request.Request(
    url,
    headers={
        "User-Agent": "xuan-weather-typhoon/2.0",
        "Accept": "application/json,*/*",
    },
)

with urllib.request.urlopen(request, timeout=30) as response:
    raw = response.read()

data = json.loads(raw)

records = data.get("records", {})

tropical_cyclones = (
    records
    .get("TropicalCyclones", {})
    .get("TropicalCyclone", [])
)

if isinstance(tropical_cyclones, dict):
    tropical_cyclones = [tropical_cyclones]

def number(value):
    if value is None or value == "":
        return None

    try:
        return float(value)
    except Exception:
        return None

def parse_analysis(item):
    return {
        "time": item.get("DateTime"),
        "lat": number(item.get("CoordinateLatitude")),
        "lon": number(item.get("CoordinateLongitude")),
        "maxWindMs": number(item.get("MaxWindSpeed")),
        "maxGustMs": number(item.get("MaxGustSpeed")),
        "pressureHpa": number(item.get("Pressure")),
        "movingSpeedKmh": number(item.get("MovingSpeed")),
        "movingDirection": item.get("MovingDirection"),
    }

def parse_forecast(item):
    return {
        "time": item.get("ForecastHour"),
        "initTime": item.get("InitialTime"),
        "lat": number(item.get("CoordinateLatitude")),
        "lon": number(item.get("CoordinateLongitude")),
        "maxWindMs": number(item.get("MaxWindSpeed")),
        "maxGustMs": number(item.get("MaxGustSpeed")),
        "pressureHpa": number(item.get("Pressure")),
        "movingSpeedKmh": number(item.get("MovingSpeed")),
        "movingDirection": item.get("MovingDirection"),
        "probabilityRadiusKm": number(
            item.get("Radius70PercentProbability")
        ),
    }

typhoons = []

for cyclone in tropical_cyclones:

    analysis_data = (
        cyclone
        .get("AnalysisData", {})
        .get("Fix", [])
    )

    forecast_data = (
        cyclone
        .get("ForecastData", {})
        .get("Fix", [])
    )

    if isinstance(analysis_data, dict):
        analysis_data = [analysis_data]

    if isinstance(forecast_data, dict):
        forecast_data = [forecast_data]

    analysis = []

    for item in analysis_data:
        parsed = parse_analysis(item)

        if (
            parsed["lat"] is not None
            and parsed["lon"] is not None
        ):
            analysis.append(parsed)

    forecast = []

    for item in forecast_data:
        parsed = parse_forecast(item)

        if (
            parsed["lat"] is not None
            and parsed["lon"] is not None
        ):
            forecast.append(parsed)

    current = analysis[-1] if analysis else None

    typhoon = {
        "year": cyclone.get("Year"),
        "internationalName": cyclone.get("TyphoonName"),
        "name": (
            cyclone.get("CwaTyphoonName")
            or cyclone.get("TyphoonName")
            or "未命名"
        ),
        "cwaTdNo": cyclone.get("CwaTdNo"),
        "cwaTyNo": cyclone.get("CwaTyNo"),
        "current": current,
        "analysis": analysis,
        "forecast": forecast,
    }

    typhoons.append(typhoon)

payload = {
    "ok": True,
    "source": "中央氣象署",
    "dataId": "W-C0034-005",
    "updatedAt": datetime.now(timezone.utc).isoformat(),
    "typhoons": typhoons,
    "typhoon": typhoons[0] if typhoons else None,
}

os.makedirs("data", exist_ok=True)

with open(OUTPUT, "w", encoding="utf-8") as file:
    json.dump(
        payload,
        file,
        ensure_ascii=False,
        indent=2,
    )

print(
    f"Saved {OUTPUT}; "
    f"typhoons={len(typhoons)}"
)

for typhoon in typhoons:
    print(
        "Typhoon:",
        typhoon["name"],
        typhoon["internationalName"],
        "CWA No:",
        typhoon["cwaTyNo"]
    )

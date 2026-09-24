import json
import os
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ.get("CWA_API_KEY", "").strip()
OUTPUT = "data/typhoon.json"

if not API_KEY:
    raise SystemExit("CWA_API_KEY GitHub Actions Secret is not set.")

URL = (
    "https://opendata.cwa.gov.tw/"
    "api/v1/rest/datastore/W-C0034-005"
)

request = urllib.request.Request(
    URL,
    headers={
        "Authorization": API_KEY,
        "Accept": "application/json",
        "User-Agent": "xuan-weather-typhoon/3.0",
    },
)

with urllib.request.urlopen(request, timeout=30) as response:
    raw = response.read()

data = json.loads(raw)

if data.get("success") not in (True, "true"):
    raise SystemExit(
        "CWA API 回傳失敗："
        + json.dumps(data, ensure_ascii=False)[:1000]
    )

records = data.get("records", {})

tropical_cyclones = (
    records
    .get("TropicalCyclones", {})
    .get("TropicalCyclone", [])
)

if isinstance(tropical_cyclones, dict):
    tropical_cyclones = [tropical_cyclones]

print(f"偵測到熱帶氣旋數量：{len(tropical_cyclones)}")


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

    current = (
        analysis[-1]
        if analysis
        else None
    )

    typhoon = {
        "year": cyclone.get("Year"),
        "internationalName": cyclone.get(
            "TyphoonName"
        ),
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

    print(
        "颱風：",
        typhoon["name"],
        "(",
        typhoon["internationalName"],
        ")",
        "CWA 編號：",
        typhoon["cwaTyNo"],
        "分析資料：",
        len(analysis),
        "筆",
        "預報資料：",
        len(forecast),
        "筆",
    )


payload = {
    "ok": True,
    "source": "中央氣象署",
    "dataId": "W-C0034-005",
    "updatedAt": datetime.now(
        timezone.utc
    ).isoformat(),
    "typhoons": typhoons,
    "typhoon": (
        typhoons[0]
        if typhoons
        else None
    ),
}


os.makedirs("data", exist_ok=True)


with open(
    OUTPUT,
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        payload,
        file,
        ensure_ascii=False,
        indent=2,
    )


print(
    f"成功寫入 {OUTPUT}"
)

print(
    f"最終熱帶氣旋數量：{len(typhoons)}"
)

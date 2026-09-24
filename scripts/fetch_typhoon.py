import json
import os
from datetime import datetime, timezone

SOURCE = "data/cwa-W-C0034-005.json"
OUTPUT = "data/typhoon.json"

if not os.path.exists(SOURCE):
    raise SystemExit(
        f"找不到 CWA 資料檔：{SOURCE}"
    )

with open(
    SOURCE,
    "r",
    encoding="utf-8"
) as file:
    data = json.load(file)


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
        "lat": number(
            item.get("CoordinateLatitude")
        ),
        "lon": number(
            item.get("CoordinateLongitude")
        ),
        "maxWindMs": number(
            item.get("MaxWindSpeed")
        ),
        "maxGustMs": number(
            item.get("MaxGustSpeed")
        ),
        "pressureHpa": number(
            item.get("Pressure")
        ),
        "movingSpeedKmh": number(
            item.get("MovingSpeed")
        ),
        "movingDirection": item.get(
            "MovingDirection"
        ),
    }


def parse_forecast(item):
    return {
        "time": item.get("ForecastHour"),
        "initTime": item.get("InitialTime"),
        "lat": number(
            item.get("CoordinateLatitude")
        ),
        "lon": number(
            item.get("CoordinateLongitude")
        ),
        "maxWindMs": number(
            item.get("MaxWindSpeed")
        ),
        "maxGustMs": number(
            item.get("MaxGustSpeed")
        ),
        "pressureHpa": number(
            item.get("Pressure")
        ),
        "movingSpeedKmh": number(
            item.get("MovingSpeed")
        ),
        "movingDirection": item.get(
            "MovingDirection"
        ),
        "probabilityRadiusKm": number(
            item.get(
                "Radius70PercentProbability"
            )
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

    if isinstance(
        analysis_data,
        dict
    ):
        analysis_data = [
            analysis_data
        ]

    if isinstance(
        forecast_data,
        dict
    ):
        forecast_data = [
            forecast_data
        ]


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
        "year": cyclone.get(
            "Year"
        ),

        "internationalName": cyclone.get(
            "TyphoonName"
        ),

        "name": (
            cyclone.get(
                "CwaTyphoonName"
            )
            or cyclone.get(
                "TyphoonName"
            )
            or "未命名"
        ),

        "cwaTdNo": cyclone.get(
            "CwaTdNo"
        ),

        "cwaTyNo": cyclone.get(
            "CwaTyNo"
        ),

        "current": current,

        "analysis": analysis,

        "forecast": forecast,
    }


    typhoons.append(typhoon)


    print(
        "偵測到颱風：",
        typhoon["name"],
        "(",
        typhoon["internationalName"],
        ")"
    )

    print(
        "CWA 編號：",
        typhoon["cwaTyNo"]
    )

    print(
        "歷史資料：",
        len(analysis),
        "筆"
    )

    print(
        "預報資料：",
        len(forecast),
        "筆"
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


os.makedirs(
    "data",
    exist_ok=True
)


with open(
    OUTPUT,
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        payload,
        file,
        ensure_ascii=False,
        indent=2
    )


print()
print(
    "================================"
)
print(
    "颱風資料更新完成"
)
print(
    f"活動中的熱帶氣旋：{len(typhoons)}"
)
print(
    f"輸出檔案：{OUTPUT}"
)
print(
    "================================"
)

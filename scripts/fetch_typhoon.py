import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
    + "&format=XML"
)

request = urllib.request.Request(
    url,
    headers={
        "User-Agent": "xuan-weather-typhoon/1.0",
        "Accept": "application/xml,text/xml,*/*",
    },
)

with urllib.request.urlopen(request, timeout=30) as response:
    xml_data = response.read()

root = ET.fromstring(xml_data)


def tag_name(element):
    return element.tag.rsplit("}", 1)[-1]


def child(element, name):
    for item in list(element):
        if tag_name(item) == name:
            return item
    return None


def value(element, name, default=None):
    item = child(element, name)

    if item is None or item.text is None:
        return default

    text = item.text.strip()

    return text if text else default


def number(value_text):
    if value_text is None:
        return None

    try:
        return float(value_text)
    except Exception:
        return None


def parse_coordinate(text_value):
    if not text_value:
        return None, None

    text_value = text_value.strip()

    # CWA 格式：經度,緯度
    parts = [x.strip() for x in text_value.split(",")]

    if len(parts) != 2:
        return None, None

    lon = number(parts[0])
    lat = number(parts[1])

    return lat, lon


def parse_fix(fix, time_field):
    coordinate_text = value(fix, "coordinate")
    lat, lon = parse_coordinate(coordinate_text)

    return {
        "time": value(fix, time_field),
        "lat": lat,
        "lon": lon,
        "maxWindMs": number(value(fix, "max_wind_speed")),
        "maxGustMs": number(value(fix, "max_gust_speed")),
        "pressureHpa": number(value(fix, "pressure")),
        "circle15km": number(value(fix, "circle_of_15ms")),
        "circle25km": number(value(fix, "circle_of_25ms")),
        "movingSpeedKmh": number(value(fix, "moving_speed")),
        "movingDirection": value(fix, "moving_direction"),
        "movingPrediction": value(fix, "moving_prediction"),
        "stateTransfer": value(fix, "state_transfer_lang"),
    }


typhoons = []

for cyclone in root.iter():

    if tag_name(cyclone) != "tropicalCyclone":
        continue

    analysis = []
    forecast = []

    for node in cyclone.iter():

        if tag_name(node) == "analysis_data":

            for fix in list(node):

                if tag_name(fix) == "fix":
                    item = parse_fix(fix, "fix_time")

                    if item["lat"] is not None and item["lon"] is not None:
                        analysis.append(item)

        elif tag_name(node) == "forecast_data":

            for fix in list(node):

                if tag_name(fix) == "fix":
                    item = parse_fix(fix, "tau")

                    if item["lat"] is not None and item["lon"] is not None:
                        forecast.append(item)

    current = analysis[-1] if analysis else None

    typhoons.append({
        "year": value(cyclone, "year"),
        "internationalName": value(cyclone, "typhoon_name"),
        "name": (
            value(cyclone, "cwa_typhoon_name")
            or value(cyclone, "typhoon_name")
            or "未命名"
        ),
        "cwaTdNo": value(cyclone, "cwa_td_no"),
        "cwaTyNo": value(cyclone, "cwa_ty_no"),
        "current": current,
        "analysis": analysis,
        "forecast": forecast,
    })


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

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SITE = "https://taiwanweather.goatcounter.com"
API = SITE + "/api/v0"
TOKEN = os.environ.get("GOATCOUNTER_API_TOKEN", "").strip()
OUT = Path("data/visitor-stats.json")
TAIPEI = ZoneInfo("Asia/Taipei")
EARLIEST_UTC = datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)


def get_json(url, token=None):
    headers = {
        "Accept": "application/json",
        "User-Agent": "XuanWeather-VisitorStats/1.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Expected JSON from {url}; got: {raw[:300]}"
                ) from exc
            if isinstance(data, dict) and ("error" in data or "errors" in data):
                raise RuntimeError(f"GoatCounter API error: {data}")
            return data
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"HTTP {exc.code} from {url}. Response: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error from {url}: {exc}") from exc


def count_value(obj, *keys):
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object; got {type(obj).__name__}")
    for key in keys:
        if key in obj and obj[key] is not None:
            value = obj[key]
            if isinstance(value, (int, float)):
                return int(value)
            digits = "".join(ch for ch in str(value) if ch.isdigit())
            if digits:
                return int(digits)
    raise ValueError(f"Cannot find count field among {keys}; response keys={list(obj)}")


def utc_param(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_visits(start_utc, end_utc):
    params = urlencode({
        "start": utc_param(start_utc),
        "end": utc_param(end_utc),
        "limit": 100,
    })
    data = get_json(f"{API}/stats/hits?{params}", TOKEN)
    if not isinstance(data, dict):
        raise ValueError("Unexpected /stats/hits response: expected a JSON object")
    if data.get("more") is True:
        raise RuntimeError(
            "GoatCounter returned more than 100 paths for this period; "
            "refusing to publish a potentially incomplete visits total."
        )
    return count_value(data, "total")


def get_pageviews(start_utc, end_utc):
    # Official endpoint for total pageviews over an explicit date range.
    params = urlencode({
        "start": utc_param(start_utc),
        "end": utc_param(end_utc),
    })
    data = get_json(f"{API}/stats/total?{params}", TOKEN)
    return count_value(data, "total")


def main():
    if not TOKEN:
        raise RuntimeError("Missing GitHub Actions secret GOATCOUNTER_API_TOKEN")

    now_local = datetime.now(TAIPEI)
    now_utc = datetime.now(timezone.utc)
    # Round down to the latest completed hour to avoid partial-hour fluctuation.
    end_utc = now_utc.replace(minute=0, second=0, microsecond=0)
    start_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_today_utc = start_today_local.astimezone(timezone.utc)

    cumulative_visits = get_visits(EARLIEST_UTC, end_utc)
    today_visits = get_visits(start_today_utc, end_utc)
    total_pageviews = get_pageviews(EARLIEST_UTC, end_utc)

    payload = {
        "source": "GoatCounter",
        "site": "taiwanweather.goatcounter.com",
        "timezone": "Asia/Taipei",
        "date_taipei": now_local.strftime("%Y-%m-%d"),
        "cumulative_visits": cumulative_visits,
        "today_visits": today_visits,
        "total_pageviews": total_pageviews,
        "today_start_taipei": start_today_local.isoformat(),
        "stats_through_taipei": end_utc.astimezone(TAIPEI).isoformat(),
        "updated_at": now_utc.isoformat(timespec="seconds"),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

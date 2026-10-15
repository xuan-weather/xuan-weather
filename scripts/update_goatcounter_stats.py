import json
import os
import re
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


def get_json(url, token=None):
    headers = {"Accept": "application/json", "User-Agent": "XuanWeather-VisitorStats/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Expected JSON from {url}; got: {raw[:300]}") from exc
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"HTTP {exc.code} from {url}. Response: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error from {url}: {exc}") from exc


def count_value(obj, *keys):
    for key in keys:
        if key in obj and obj[key] is not None:
            value = obj[key]
            if isinstance(value, (int, float)):
                return int(value)
            match = re.search(r"[\d,]+", str(value))
            if match:
                return int(match.group(0).replace(",", ""))
    raise ValueError(f"Cannot find count field among {keys}; response keys={list(obj)}")


def main():
    if not TOKEN:
        raise RuntimeError("Missing GitHub Actions secret GOATCOUNTER_API_TOKEN")

    # Public GoatCounter counter is used for cumulative pageviews.
    counter = get_json(SITE + "/counter/TOTAL.json")
    total_pageviews = count_value(counter, "count", "count_unique")

    # Overall visits count from the authenticated stats endpoint.
    overall = get_json(f"{API}/stats/total", TOKEN)
    cumulative_visits = count_value(overall, "total")

    # Today's visits are measured from Taiwan midnight to the latest full hour.
    now = datetime.now(TAIPEI)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = now.replace(minute=0, second=0, microsecond=0)
    params = urlencode({
        "start": start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    daily = get_json(f"{API}/stats/total?{params}", TOKEN)
    today_visits = count_value(daily, "total")

    payload = {
        "source": "GoatCounter",
        "site": "taiwanweather.goatcounter.com",
        "timezone": "Asia/Taipei",
        "date_taipei": now.strftime("%Y-%m-%d"),
        "cumulative_visits": cumulative_visits,
        "today_visits": today_visits,
        "total_pageviews": total_pageviews,
        "today_start_taipei": start_local.isoformat(),
        "stats_through_taipei": end_local.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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

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
    headers = {
        "Accept": "application/json",
        "User-Agent": "XuanWeather-VisitorStats/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Expected JSON but received non-JSON from {url}: {raw[:300]}"
                ) from exc
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"HTTP {exc.code} from {url}. Response: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error from {url}: {exc}") from exc


def parse_count(value):
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"[\d,]+", str(value or ""))
    if not match:
        raise ValueError(f"Cannot parse count value: {value!r}")
    return int(match.group(0).replace(",", ""))


def main():
    if not TOKEN:
        raise RuntimeError("Missing GitHub Actions secret GOATCOUNTER_API_TOKEN")

    public = get_json(SITE + "/counter/TOTAL.json")
    cumulative = parse_count(public.get("count", public.get("count_unique")))

    now = datetime.now(TAIPEI)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = now.replace(minute=0, second=0, microsecond=0)
    query = urlencode({
        "start": start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    # GoatCounter documented endpoint is singular: stats/total.
    stats = get_json(f"{API}/stats/total?{query}", TOKEN)
    today = parse_count(stats.get("total"))

    payload = {
        "source": "GoatCounter",
        "site": "taiwanweather.goatcounter.com",
        "timezone": "Asia/Taipei",
        "date_taipei": now.strftime("%Y-%m-%d"),
        "cumulative_total": cumulative,
        "today_total": today,
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

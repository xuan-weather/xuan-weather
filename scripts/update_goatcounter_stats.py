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
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error from {url}: {exc}") from exc


def parse_count(value):
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"[\d,]+", str(value or ""))
    if not match:
        raise ValueError(f"Cannot parse GoatCounter count: {value!r}")
    return int(match.group(0).replace(",", ""))


def main():
    if not TOKEN:
        raise RuntimeError("Missing GOATCOUNTER_API_TOKEN GitHub Actions secret")

    # Public GoatCounter TOTAL counter provides the lifetime count.
    public_total = get_json(SITE + "/counter/TOTAL.json")
    cumulative_total = parse_count(public_total.get("count", public_total.get("count_unique")))

    # Calculate today's date boundary in Taiwan local time, then convert to UTC.
    now_taipei = datetime.now(TAIPEI)
    start_local = now_taipei.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = now_taipei.replace(minute=0, second=0, microsecond=0)
    if end_local <= start_local:
        end_local = start_local.replace(hour=23, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    params = urlencode({
        "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    # Newer GoatCounter deployments expose the aggregate endpoint as /stats/totals.
    # Keep /stats/total as a compatibility fallback for older deployments.
    try:
        daily = get_json(f"{API}/stats/totals?{params}", TOKEN)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        daily = get_json(f"{API}/stats/total?{params}", TOKEN)

    today_total = parse_count(daily.get("total"))

    payload = {
        "source": "GoatCounter",
        "site": "taiwanweather.goatcounter.com",
        "timezone": "Asia/Taipei",
        "date_taipei": now_taipei.strftime("%Y-%m-%d"),
        "cumulative_total": cumulative_total,
        "today_total": today_total,
        "today_start_taipei": start_local.isoformat(),
        "stats_through_taipei": end_local.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k not in ("site",)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

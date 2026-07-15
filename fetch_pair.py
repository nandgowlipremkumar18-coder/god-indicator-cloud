"""
fetch_pair.py — Subprocess worker for god_engine.py
Downloads OHLCV data via direct Yahoo Finance REST API and prints JSON to stdout.
Called by subprocess.run(timeout=30) which sends OS-level SIGKILL if needed.
No yfinance dependency — pure requests.get() for minimum import overhead.
"""
import sys
import json
import time
import requests

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: fetch_pair.py TICKER INTERVAL"}))
        return

    ticker   = sys.argv[1]   # e.g. "EURUSD=X", "GC=F", "BTC-USD"
    htf      = sys.argv[2]   # e.g. "1h", "30m", "15m"

    interval_map = {"1h": "60m", "30m": "30m", "15m": "15m"}
    yahoo_interval = interval_map.get(htf, "60m")

    end_ts   = int(time.time())
    start_ts = end_ts - (60 * 24 * 3600)  # 60 days of history

    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1":  start_ts,
        "period2":  end_ts,
        "interval": yahoo_interval,
        "events":   "history",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    try:
        # timeout=25: per-packet socket timeout.
        # Combined with subprocess.run(timeout=30) in the caller,
        # this process is ALWAYS killed at the OS level within 30s.
        resp = requests.get(url, params=params, headers=headers, timeout=25)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("chart", {}).get("result")
        if not result:
            print(json.dumps({"error": "no result in chart"}))
            return

        chart      = result[0]
        timestamps = chart.get("timestamp", [])
        if not timestamps:
            print(json.dumps({"error": "no timestamps"}))
            return

        quote = chart["indicators"]["quote"][0]
        opens   = quote.get("open",   [None] * len(timestamps))
        highs   = quote.get("high",   [None] * len(timestamps))
        lows    = quote.get("low",    [None] * len(timestamps))
        closes  = quote.get("close",  [None] * len(timestamps))
        volumes = quote.get("volume", [0]    * len(timestamps))

        records = []
        for i, ts in enumerate(timestamps):
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
            if o is None or h is None or l is None or c is None:
                continue
            records.append({
                "t": ts,          # Unix timestamp (integer)
                "o": float(o),
                "h": float(h),
                "l": float(l),
                "c": float(c),
                "v": float(v) if v is not None else 0.0,
            })

        if len(records) == 0:
            print(json.dumps({"error": "all rows null"}))
            return

        print(json.dumps({"ok": True, "rows": len(records), "data": records}))

    except requests.exceptions.Timeout:
        print(json.dumps({"error": "timeout"}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    main()

"""
fetch_pair.py — Standalone subprocess worker.
Downloads OHLCV data for one pair and prints JSON to stdout.
Called by god_engine.py with subprocess.run(timeout=30) for hard kill timeout.
"""
import sys, os, json
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import requests

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: fetch_pair.py TICKER INTERVAL"}))
        return

    ticker   = sys.argv[1]
    interval = sys.argv[2]

    try:
        # Browser session to bypass Yahoo Finance cloud-IP blocking
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept":          "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })

        df = yf.download(
            ticker, period="60d", interval=interval,
            progress=False, auto_adjust=True, session=session
        )

        if df is None or df.empty:
            print(json.dumps({"error": "empty"}))
            return

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()

        records = []
        for ts, row in df.iterrows():
            records.append({
                "t": str(ts),
                "o": round(float(row["Open"]),  8),
                "h": round(float(row["High"]),  8),
                "l": round(float(row["Low"]),   8),
                "c": round(float(row["Close"]), 8),
                "v": round(float(row["Volume"]),2),
            })

        print(json.dumps({"ok": True, "rows": len(records), "data": records}))

    except Exception as e:
        print(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    main()

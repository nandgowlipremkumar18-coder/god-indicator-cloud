"""
God Indicator Engine v2.0 — Phase 2
Exact Python translation of Pine Script v2.3
"God Indicator (v2.3 - Auto | MD)" by cyatophilum & Prem Kumar

Phase 2 additions:
  - Confluence Score (1-5 stars per signal)
  - Alert Cooldown (configurable, default 30 min)
  - Per-pair enabled/disabled support
  - Smarter alert thresholds
"""

import numpy as np
import pandas as pd
import yfinance as yf
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, List
from enum import Enum


# ─── PAIR CONFIG ──────────────────────────────────────────────────────────────
@dataclass
class PairConfig:
    name:      str
    yf_ticker: str
    htf:       str
    smooth:    int
    nr_pct:    float
    consec:    int
    range_len: int = 30
    hma_len:   int = 8


PAIR_CONFIGS: Dict[str, PairConfig] = {
    # HTF = 1H — matches TradingView God Indicator v2.3 (HTF: 1H)
    # consec = min 4 NR candles required (stricter = better quality signals)
    # Each pair has unique Smooth and NR% parameters
    #                   name      yf_ticker   htf   smooth  nr_pct  consec
    "EURUSD": PairConfig("EURUSD", "EURUSD=X", "1h",   4,   78.0,    4),
    "XAUUSD": PairConfig("XAUUSD", "GC=F",     "1h",   5,   75.0,    4),  # was 3 → 4
    "GBPUSD": PairConfig("GBPUSD", "GBPUSD=X", "1h",   6,   72.0,    4),  # was 3 → 4
    "USDCAD": PairConfig("USDCAD", "USDCAD=X", "1h",   4,   78.0,    4),  # was 3 → 4
    "XAGUSD": PairConfig("XAGUSD", "SI=F",     "1h",   6,   70.0,    4),  # was 3 → 4
    "GBPJPY": PairConfig("GBPJPY", "GBPJPY=X", "1h",   6,   70.0,    4),  # was 3 → 4
    "AUDUSD": PairConfig("AUDUSD", "AUDUSD=X", "1h",   5,   75.0,    4),  # was 3 → 4
    "USDJPY": PairConfig("USDJPY", "USDJPY=X", "1h",   5,   75.0,    4),  # was 3 → 4
    "USDCHF": PairConfig("USDCHF", "USDCHF=X", "1h",   4,   80.0,    4),
    "EURJPY": PairConfig("EURJPY", "EURJPY=X", "1h",   6,   72.0,    4),  # was 3 → 4
    "BTCUSD": PairConfig("BTCUSD", "BTC-USD",  "1h",   6,   70.0,    4),
    "NZDUSD": PairConfig("NZDUSD", "NZDUSD=X", "1h",   5,   75.0,    4),
    "EURGBP": PairConfig("EURGBP", "EURGBP=X", "1h",   4,   82.0,    5),  # keep 5 (tightest)
}


class SignalType(Enum):
    NONE          = "none"
    LONG          = "long"
    SHORT         = "short"
    CONSOLIDATING = "consolidating"


# ─── PAIR STATUS (Phase 2) ────────────────────────────────────────────────────
@dataclass
class PairStatus:
    name:               str
    signal:             SignalType      = SignalType.NONE
    cpt:                int             = 0
    consec:             int             = 3
    is_narrow:          bool            = False
    price:              float           = 0.0
    last_signal_time:   Optional[str]   = None
    last_check_time:    Optional[str]   = None
    last_signal_bar:    Optional[str]   = None
    error:              Optional[str]   = None
    htf:                str             = "15m"
    # Phase 2
    confluence_score:   int             = 0      # 0-5 stars
    last_alert_dt:      Optional[datetime] = None  # for cooldown tracking
    is_enabled:         bool            = True    # per-pair ON/OFF
    cooldown_remaining: int             = 0      # seconds left in cooldown
    # Phase 2 — Set notifications
    last_set_notified:  int             = 0   # last set number we alerted on
    set_number:         int             = 0   # current set number (cpt // consec)


# ─── MATH: Pine Script translations ──────────────────────────────────────────
def _wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    wsum    = weights.sum()
    return series.rolling(length).apply(lambda x: np.dot(x, weights) / wsum, raw=True)


def _hma(series: pd.Series, length: int) -> pd.Series:
    half_len = max(1, length // 2)
    sqrt_len = max(1, int(np.floor(np.sqrt(length))))
    raw      = 2.0 * _wma(series, half_len) - _wma(series, length)
    return _wma(raw, sqrt_len)


def _rma(series: pd.Series, length: int) -> pd.Series:
    alpha  = 1.0 / length
    values = series.values.astype(float)
    result = np.full(len(values), np.nan)
    start  = 0
    while start < len(values) and np.isnan(values[start]):
        start += 1
    if start >= len(values):
        return pd.Series(result, index=series.index)
    result[start] = values[start]
    for i in range(start + 1, len(values)):
        result[i] = alpha * values[i] + (1.0 - alpha) * result[i - 1]
    return pd.Series(result, index=series.index)


def _rising(series: pd.Series, length: int = 2) -> pd.Series:
    result = pd.Series(True, index=series.index)
    for i in range(1, length + 1):
        result = result & (series > series.shift(i))
    return result


def _falling(series: pd.Series, length: int = 2) -> pd.Series:
    result = pd.Series(True, index=series.index)
    for i in range(1, length + 1):
        result = result & (series < series.shift(i))
    return result


# ─── SIGNAL CALCULATION ───────────────────────────────────────────────────────
def calculate_signals(df: pd.DataFrame, config: PairConfig) -> pd.DataFrame:
    """Exact Python translation of God Indicator v2.3 Pine Script."""
    df           = df.copy()
    candle_range = df["High"] - df["Low"]
    price_range  = candle_range.rolling(config.smooth).mean()
    avg_range    = _rma(price_range, config.range_len)
    narrow_range = price_range < (config.nr_pct / 100.0) * avg_range

    nr_vals  = narrow_range.values
    cpt_vals = np.zeros(len(df), dtype=int)
    for i in range(len(nr_vals)):
        if i == 0:
            cpt_vals[i] = 1 if nr_vals[i] else 0
        else:
            cpt_vals[i] = (cpt_vals[i - 1] + 1) if nr_vals[i] else 0

    df["narrow_range"] = narrow_range
    df["cpt"]          = cpt_vals
    df["breakout"]     = (df["cpt"].shift(1) >= config.consec) & (~df["narrow_range"])

    hma_s          = _hma(df["Close"], config.hma_len)
    df["hma"]        = hma_s
    df["is_bullish"] = _rising(hma_s, 2)
    df["is_bearish"] = _falling(hma_s, 2)

    df["long_signal"]  = df["breakout"] & df["is_bullish"]
    df["short_signal"] = df["breakout"] & df["is_bearish"]
    return df


def fetch_htf_data(config: PairConfig) -> Optional[pd.DataFrame]:
    try:
        period_map = {"15m": "60d", "30m": "60d", "1h": "60d"}
        period = period_map.get(config.htf, "60d")
        df = yf.download(config.yf_ticker, period=period,
                         interval=config.htf, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) < config.range_len * 3 + config.smooth + 5:
            return None
        return df
    except Exception:
        return None


def send_alert(topic: str, title: str, body: str,
               priority: str = "urgent", tags: str = "chart_increasing"):
    try:
        requests.post(f"https://ntfy.sh/{topic}",
                      data=body.encode("utf-8"),
                      headers={"Title": title, "Priority": priority, "Tags": tags},
                      timeout=8)
    except Exception as e:
        print(f"[God Engine] Alert error: {e}")


# ─── SESSION DETECTION ────────────────────────────────────────────────────────
TRADING_SESSIONS = {
    "london":   (7,  16),
    "new_york": (12, 21),
}

def in_active_session() -> bool:
    """Returns True if London or New York session is currently open."""
    h = datetime.now(timezone.utc).hour
    return any(s <= h < e for s, e in TRADING_SESSIONS.values())


# ─── CONFLUENCE SCORING (Phase 2) ─────────────────────────────────────────────
def _ordinal(n: int) -> str:
    """Return 1st, 2nd, 3rd, 4th..."""
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")
    return f"{n}{suffix}"


def calc_confluence(status: PairStatus, config: PairConfig,
                    cooldown_minutes: int) -> int:
    """
    Score a signal from 0-5 stars based on quality factors.
    Only called when a breakout signal is detected.
    """
    score = 0

    # Star 1: Signal fired (always true when this is called)
    score += 1

    # Star 2: Active trading session (London or NY)
    if in_active_session():
        score += 1

    # Star 3: NR count >= 50% of required consecutive candles
    if status.cpt >= max(1, config.consec // 2):
        score += 1

    # Star 4: Cooldown is clear (no recent alert for this pair)
    if status.last_alert_dt is None:
        score += 1
    else:
        elapsed = (datetime.now() - status.last_alert_dt).total_seconds() / 60
        if elapsed >= cooldown_minutes:
            score += 1

    # Star 5: NR count at MAXIMUM (fully built squeeze)
    if status.cpt >= config.consec:
        score += 1

    return min(score, 5)


STAR_LABELS  = ["", "LOW", "LOW", "MEDIUM", "HIGH", "MAX"]
STAR_PRIORITY = {1: None, 2: None, 3: "default", 4: "high", 5: "urgent"}


# ─── ENGINE ───────────────────────────────────────────────────────────────────
class GodWatcherEngine:
    def __init__(self, ntfy_topic: str = "god-indicator",
                 check_interval: int = 300,
                 cooldown_minutes: int = 30,
                 min_stars: int = 3):
        self.ntfy_topic       = ntfy_topic
        self.check_interval   = check_interval
        self.cooldown_minutes = cooldown_minutes
        self.min_stars        = min_stars
        self.running          = False
        self._lock            = threading.Lock()   # for status data
        self._log_lock        = threading.Lock()   # lightweight — log lines only
        self._callbacks: List[Callable] = []
        self._thread: Optional[threading.Thread] = None
        self.log_lines: List = []

        self.statuses: Dict[str, PairStatus] = {
            name: PairStatus(name=name, consec=cfg.consec, htf=cfg.htf)
            for name, cfg in PAIR_CONFIGS.items()
        }
        self._scan_running = False   # guard — prevents double scans

    def add_update_callback(self, fn: Callable):
        self._callbacks.append(fn)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._engine_loop, daemon=True)
        self._thread.start()
        self._log("God Engine v2.0 started - monitoring all pairs", "green")

    def stop(self):
        self.running = False
        self._log("God Engine stopped", "dim")

    def force_scan(self):
        if self._scan_running:
            self._log("Scan already in progress — please wait", "orange")
            return
        threading.Thread(target=self._scan_all, daemon=True).start()

    def set_pair_enabled(self, name: str, enabled: bool):
        with self._lock:
            if name in self.statuses:
                self.statuses[name].is_enabled = enabled

    def _engine_loop(self):
        try:
            self._scan_all()
        except Exception as e:
            self._log(f"Initial scan failed: {e}", "red")
        while self.running:
            for _ in range(self.check_interval):
                if not self.running:
                    return
                time.sleep(1)
            if self.running:
                try:
                    self._scan_all()
                except Exception as e:
                    self._log(f"Scan failed, will retry next cycle: {e}", "red")

    def _scan_all(self):
        if self._scan_running:
            self._log("Scan already in progress — please wait", "orange")
            return
        self._scan_running = True
        enabled = [n for n, s in self.statuses.items() if s.is_enabled]
        self._log(f"Scanning {len(enabled)}/{len(PAIR_CONFIGS)} pairs...", "dim")
        try:
            # One pair at a time — UI stays smooth, no GIL freeze
            with ThreadPoolExecutor(max_workers=1) as pool:
                futures = {pool.submit(self._check_pair, n, PAIR_CONFIGS[n]): n
                           for n in enabled}
                try:
                    for fut in as_completed(futures, timeout=300):
                        name = futures[fut]
                        try:
                            fut.result()
                        except Exception as e:
                            self._log(f"{name} error: {e}", "red")
                except TimeoutError:
                    self._log("Scan timed out — some pairs skipped", "orange")
        finally:
            self._scan_running = False
        self._notify_ui()
        self._log(f"Scan complete. Next scan in {self.check_interval // 60}m", "dim")

    def _check_pair(self, name: str, config: PairConfig):
        time.sleep(0)          # yield GIL → keeps Tkinter UI thread responsive
        status = self.statuses[name]

        # Update cooldown countdown
        with self._lock:
            if status.last_alert_dt:
                elapsed = (datetime.now() - status.last_alert_dt).total_seconds()
                remaining = max(0, self.cooldown_minutes * 60 - int(elapsed))
                status.cooldown_remaining = remaining
            else:
                status.cooldown_remaining = 0

        try:
            df = fetch_htf_data(config)
            if df is None:
                with self._lock:
                    status.error = "No data"
                return

            df = calculate_signals(df, config)
            if len(df) < 2:
                return

            last      = df.iloc[-2]
            bar_ts    = str(df.index[-2])
            cur_price = float(df["Close"].iloc[-1])
            cur_cpt   = int(last["cpt"])

            with self._lock:
                status.price           = cur_price
                status.cpt             = cur_cpt
                status.is_narrow       = bool(last["narrow_range"])
                status.last_check_time = datetime.now().strftime("%H:%M:%S")
                status.error           = None

                # ── SET COMPLETION NOTIFICATION ──────────────────────────────
                if cur_cpt > 0 and config.consec > 0:
                    current_set = cur_cpt // config.consec
                    status.set_number = current_set
                    if current_set >= 1 and current_set != status.last_set_notified:
                        status.last_set_notified = current_set
                        ordinal = _ordinal(current_set)
                        extra = " -- STRONG SQUEEZE!" if current_set == 2 else (
                                " -- VERY STRONG!" if current_set >= 3 else "")
                        p_str = f"{cur_price:.5f}" if cur_price < 100 else f"{cur_price:.2f}"
                        msg = (f"{ordinal} Set Complete - {name}\n"
                               f"{cur_cpt} consecutive NR candles{extra}\n"
                               f"Price: {p_str}\n"
                               f"Time: {datetime.now().strftime('%H:%M')} IST")
                        self._log(
                            f"{ordinal} set complete on {name} "
                            f"({cur_cpt} NR candles){extra}", "orange"
                        )
                        priority = "urgent" if current_set >= 2 else "default"
                        threading.Thread(
                            target=send_alert,
                            args=(self.ntfy_topic,
                                  f"{ordinal} Set - {name} NR Squeeze",
                                  msg, priority, "fire" if current_set >= 2 else "eyes"),
                            daemon=True).start()
                else:
                    # Reset set counter when squeeze breaks
                    if cur_cpt == 0:
                        status.last_set_notified = 0
                        status.set_number = 0

                if last["long_signal"]:
                    status.signal = SignalType.LONG
                    if status.last_signal_bar != bar_ts:
                        status.last_signal_bar  = bar_ts
                        status.last_signal_time = datetime.now().strftime("%H:%M:%S")
                        score = calc_confluence(status, config, self.cooldown_minutes)
                        status.confluence_score = score
                        stars = "*" * score
                        self._log(
                            f"LONG SIGNAL - {name} @ {cur_price:.5f}  [{stars}] {STAR_LABELS[score]}",
                            "green"
                        )
                        if score >= self.min_stars and status.cooldown_remaining == 0:
                            priority = STAR_PRIORITY.get(score, "default")
                            status.last_alert_dt = datetime.now()
                            threading.Thread(
                                target=send_alert,
                                args=(self.ntfy_topic,
                                      f"LONG BREAKOUT - {name} ({stars})",
                                      f"God Indicator LONG signal on {name}\n"
                                      f"Score: {score}/5 stars - {STAR_LABELS[score]}\n"
                                      f"NR: {int(last['cpt'])}/{config.consec} | "
                                      f"Session: {'ACTIVE' if in_active_session() else 'LOW'}\n"
                                      f"Price: {cur_price:.5f}\n"
                                      f"Time: {datetime.now().strftime('%H:%M')} IST",
                                      priority, "chart_increasing"),
                                daemon=True).start()
                        elif score < self.min_stars:
                            self._log(
                                f"  (No alert: {score} stars < min {self.min_stars} required)",
                                "dim"
                            )
                        elif status.cooldown_remaining > 0:
                            mins = status.cooldown_remaining // 60
                            self._log(
                                f"  (No alert: cooldown active, {mins}m remaining)", "dim"
                            )

                elif last["short_signal"]:
                    status.signal = SignalType.SHORT
                    if status.last_signal_bar != bar_ts:
                        status.last_signal_bar  = bar_ts
                        status.last_signal_time = datetime.now().strftime("%H:%M:%S")
                        score = calc_confluence(status, config, self.cooldown_minutes)
                        status.confluence_score = score
                        stars = "*" * score
                        self._log(
                            f"SHORT SIGNAL - {name} @ {cur_price:.5f}  [{stars}] {STAR_LABELS[score]}",
                            "red"
                        )
                        if score >= self.min_stars and status.cooldown_remaining == 0:
                            priority = STAR_PRIORITY.get(score, "default")
                            status.last_alert_dt = datetime.now()
                            threading.Thread(
                                target=send_alert,
                                args=(self.ntfy_topic,
                                      f"SHORT BREAKOUT - {name} ({stars})",
                                      f"God Indicator SHORT signal on {name}\n"
                                      f"Score: {score}/5 stars - {STAR_LABELS[score]}\n"
                                      f"NR: {int(last['cpt'])}/{config.consec} | "
                                      f"Session: {'ACTIVE' if in_active_session() else 'LOW'}\n"
                                      f"Price: {cur_price:.5f}\n"
                                      f"Time: {datetime.now().strftime('%H:%M')} IST",
                                      priority, "chart_with_downwards_trend"),
                                daemon=True).start()
                        elif score < self.min_stars:
                            self._log(
                                f"  (No alert: {score} stars < min {self.min_stars} required)",
                                "dim"
                            )
                        elif status.cooldown_remaining > 0:
                            mins = status.cooldown_remaining // 60
                            self._log(
                                f"  (No alert: cooldown active, {mins}m remaining)", "dim"
                            )

                elif status.is_narrow and status.cpt > 0:
                    status.signal           = SignalType.CONSOLIDATING
                    status.confluence_score = 0
                else:
                    status.signal           = SignalType.NONE
                    status.confluence_score = 0

        except Exception as e:
            with self._lock:
                status.error = str(e)[:60]

    def _log(self, message: str, tag: str = ""):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._log_lock:          # lightweight lock — never blocks UI thread
            self.log_lines.append((f"[{ts}]  {message}", tag))
            if len(self.log_lines) > 300:
                self.log_lines = self.log_lines[-300:]

    def _notify_ui(self):
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass

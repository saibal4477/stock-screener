import datetime
import os
import pytz
import numpy as np
import pandas as pd
import yfinance as yf
from typing import List, Dict, Any, Optional

import config
from core.db import get_company_metadata

def get_market_status() -> Dict[str, Any]:
    ny_tz = pytz.timezone('America/New_York')
    now = datetime.datetime.now(ny_tz)
    weekday = now.weekday() # 0=Monday, 6=Sunday
    is_weekend = weekday >= 5
    
    # Regular trading hours: 9:30 AM to 4:00 PM EST Monday-Friday
    mkt_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    mkt_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    is_open = (not is_weekend) and (mkt_open <= now <= mkt_close)
    
    # Silent Flip intraday window: strictly 10:00 AM to 4:00 PM EST on weekdays
    scan_start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    is_scanning_hours = (not is_weekend) and (scan_start <= now <= mkt_close)
    
    status_str = "OPEN (Live Market)" if is_open else ("CLOSED (Weekend)" if is_weekend else "CLOSED (After Hours / Pre-Market)")
    
    # Calculate next trading day 10:00 AM EST
    target_next_scan = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= target_next_scan or is_weekend:
        days_ahead = 1
        candidate = now + datetime.timedelta(days=days_ahead)
        while candidate.weekday() >= 5:
            days_ahead += 1
            candidate = now + datetime.timedelta(days=days_ahead)
        target_next_scan = candidate.replace(hour=10, minute=0, second=0, microsecond=0)

    seconds_to_next_scan = max(0, int((target_next_scan - now).total_seconds()))

    def fmt_seconds(s):
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h > 0:
            return f"{h}h {m}m {sec}s"
        return f"{m}m {sec}s"
    
    return {
        "is_open": is_open,
        "is_scanning_hours": is_scanning_hours,
        "is_weekend": is_weekend,
        "current_time_est": now.strftime("%Y-%m-%d %H:%M:%S EST"),
        "status_label": status_str,
        "next_scan_time_est": target_next_scan.strftime("%Y-%m-%d 10:00:00 EST"),
        "countdown_to_scan": fmt_seconds(seconds_to_next_scan),
        "seconds_to_next_scan": seconds_to_next_scan
    }

import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# Global in-memory cache for 15-minute intraday bars (5-minute TTL)
# Prevents Yahoo Finance 429 Rate Limiting across repeated scans and chart views
_INTRA_BARS_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300 # 5 minutes

def get_cached_bar(ticker: str) -> Optional[pd.DataFrame]:
    """Retrieve cached 15-minute DataFrame if still fresh (< 5 minutes)."""
    item = _INTRA_BARS_CACHE.get(ticker.upper())
    if item and (time.time() - item.get('ts', 0) < CACHE_TTL_SECONDS):
        return item.get('df')
    return None

def set_cached_bar(ticker: str, df: pd.DataFrame):
    """Store 15-minute DataFrame in cache with current timestamp."""
    if df is not None and not df.empty:
        _INTRA_BARS_CACHE[ticker.upper()] = {'df': df, 'ts': time.time()}

def get_yf_session() -> requests.Session:
    """Creates a resilient HTTP session with rotating browser headers and exponential backoff retry."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9'
    })
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=25, pool_maxsize=25)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

def scan_live_market(
    tickers: List[str],
    direction_filter: str = "SHORTS_ONLY",
    min_planned_rr: float = 1.5,
    target_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Downloads live/recent 15-minute intraday bars via Yahoo Finance API with rate-limit protection,
    caches data in RAM (5-min TTL), establishes the 30-minute Opening Range (9:30 - 10:00 AM EST),
    and scans all subsequent candles (after 10:00 AM EST) for Silent Flip opportunities.
    """
    if not tickers:
        return {"status": "error", "message": "No tickers provided", "opportunities": []}

    meta_map = get_company_metadata()
    clean_tickers = [t.strip().upper() for t in tickers if t.strip()]

    dfs_by_ticker: Dict[str, pd.DataFrame] = {}
    tickers_to_fetch: List[str] = []

    # 1. Cache-first lookup: Reuse fresh bars to avoid hitting Yahoo Finance rate limits
    for t in clean_tickers:
        cached = get_cached_bar(t)
        if cached is not None and not cached.empty:
            dfs_by_ticker[t] = cached
        else:
            tickers_to_fetch.append(t)

    # 2. Fetch remaining uncached tickers in polite batches
    if tickers_to_fetch:
        session = get_yf_session()
        batch_size = 50
        batches = [tickers_to_fetch[i:i + batch_size] for i in range(0, len(tickers_to_fetch), batch_size)]

        for idx, batch in enumerate(batches):
            try:
                # Polite spacing between batches to prevent 429 throttling
                if idx > 0:
                    time.sleep(0.35)

                b_data = yf.download(
                    batch, 
                    period="5d", 
                    interval="15m", 
                    group_by="ticker", 
                    progress=False, 
                    threads=True,
                    session=session
                )
                if b_data is None or b_data.empty:
                    continue
                
                if len(batch) == 1:
                    t = batch[0]
                    b_df = b_data.dropna().copy()
                    if not b_df.empty:
                        dfs_by_ticker[t] = b_df
                        set_cached_bar(t, b_df)
                else:
                    if isinstance(b_data.columns, pd.MultiIndex):
                        avail_tickers = set(b_data.columns.get_level_values(0))
                        for t in batch:
                            if t in avail_tickers:
                                try:
                                    t_df = b_data[t].dropna().copy()
                                    if not t_df.empty:
                                        dfs_by_ticker[t] = t_df
                                        set_cached_bar(t, t_df)
                                except Exception:
                                    pass
            except Exception:
                continue

    if not dfs_by_ticker:
        return {"status": "error", "message": "No intraday data available from Yahoo Finance (Rate limited or market closed)", "opportunities": []}

    setups = []
    scanned_dates = set()

    for ticker, df in dfs_by_ticker.items():
        try:
            # Ensure tz is Eastern
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
            else:
                df.index = df.index.tz_convert('America/New_York')

            # Identify target date (either user specified or latest available session)
            available_dates = df.index.strftime('%Y-%m-%d').unique().tolist()
            if not available_dates:
                continue

            session_date = target_date if (target_date and target_date in available_dates) else available_dates[-1]
            scanned_dates.add(session_date)

            df_day = df[df.index.strftime('%Y-%m-%d') == session_date].copy()
            if len(df_day) < 3:
                # Need at least 2 opening range bars (09:30, 09:45) + at least 1 test bar (10:00)
                continue

            # 1. Opening Range: First two 15-minute candles (9:30 AM to 10:00 AM EST)
            c0 = df_day.iloc[0]
            c1 = df_day.iloc[1]
            rh = float(max(c0['High'], c1['High']))
            rl = float(min(c0['Low'], c1['Low']))
            fh = float(max(max(c0['Open'], c0['Close']), max(c1['Open'], c1['Close'])))
            fl = float(min(min(c0['Open'], c0['Close']), min(c1['Open'], c1['Close'])))

            # 2. ATR(14) for Silent Candle filter
            atr14 = float((df['High'] - df['Low']).rolling(14).mean().iloc[-1])
            if np.isnan(atr14) or atr14 <= 0:
                atr14 = float(rh - rl) if (rh - rl) > 0 else 1.0

            # 3. Scan all subsequent candles formed AFTER 30 minutes (from Candle 2 / 10:00 AM onwards)
            for i in range(2, len(df_day)):
                c = df_day.iloc[i]
                c_prev = df_day.iloc[i - 1]
                t_str = c.name.strftime('%H:%M EST')
                c_range = float(c['High'] - c['Low'])

                # Silent Candle Condition: candle range <= 65% of ATR(14)
                if c_range > 0.65 * atr14:
                    continue

                silent_ratio_pct = round((c_range / atr14) * 100, 1)

                # --- SHORT SCENARIO: Fading Range High (RH) or Flip High (FH) ---
                if direction_filter in ['SHORTS_ONLY', 'ALL']:
                    tested_short = False
                    lvl_type = None
                    lvl_price = 0.0

                    # Test RH
                    if abs(c['High'] - rh) / rh <= 0.0025 or (c['High'] >= rh and c['Close'] <= rh):
                        tested_short = True
                        lvl_type = 'RH'
                        lvl_price = rh
                    # Test FH
                    elif abs(c['High'] - fh) / fh <= 0.0025 or (c['High'] >= fh and c['Close'] <= fh):
                        tested_short = True
                        lvl_type = 'FH'
                        lvl_price = fh

                    if tested_short:
                        entry = round(float(c['Low'] - 0.01), 2)
                        stop = round(float(c['High'] + 0.01), 2)
                        target = round(rl, 2)
                        risk = round(stop - entry, 2)
                        reward = round(entry - target, 2)

                        if risk > 0 and reward > 0:
                            rr = round(reward / risk, 2)
                            if rr >= min_planned_rr:
                                # Determine actual outcome across subsequent candles
                                outcome = "ACTIVE_IN_PLAY"
                                exit_price = None
                                exit_time = None
                                realized_r = None

                                for j in range(i + 1, len(df_day)):
                                    sub_c = df_day.iloc[j]
                                    if sub_c['High'] >= stop:
                                        outcome = "STOPPED_OUT"
                                        exit_price = stop
                                        exit_time = sub_c.name.strftime('%H:%M EST')
                                        realized_r = -1.0
                                        break
                                    elif sub_c['Low'] <= target:
                                        outcome = "TARGET_HIT"
                                        exit_price = target
                                        exit_time = sub_c.name.strftime('%H:%M EST')
                                        realized_r = rr
                                        break

                                if outcome == "ACTIVE_IN_PLAY" and len(df_day) > i + 1:
                                    last_c = df_day.iloc[-1]
                                    exit_price = round(float(last_c['Close']), 2)
                                    exit_time = last_c.name.strftime('%H:%M EST')
                                    realized_r = round((entry - exit_price) / risk, 2) if risk > 0 else 0.0
                                    outcome = "CLOSED_AT_EOD"

                                info = meta_map.get(ticker, {})
                                setups.append({
                                    "ticker": ticker,
                                    "company_name": info.get("name", ticker),
                                    "sector": info.get("sector", "Other"),
                                    "session_date": session_date,
                                    "setup_time": t_str,
                                    "direction": "SHORT",
                                    "tested_level": lvl_type,
                                    "level_price": round(lvl_price, 2),
                                    "rh": round(rh, 2),
                                    "rl": round(rl, 2),
                                    "entry_price": entry,
                                    "stop_loss": stop,
                                    "target_profit": target,
                                    "dollar_risk": risk,
                                    "dollar_reward": reward,
                                    "planned_rr": rr,
                                    "silent_pct": silent_ratio_pct,
                                    "status": outcome,
                                    "exit_price": exit_price,
                                    "exit_time": exit_time,
                                    "realized_r": realized_r
                                })

                # --- LONG SCENARIO: Buying Range Low (RL) or Flip Low (FL) ---
                if direction_filter in ['LONGS_ONLY', 'ALL']:
                    tested_long = False
                    lvl_type = None
                    lvl_price = 0.0

                    if abs(c['Low'] - rl) / rl <= 0.0025 or (c['Low'] <= rl and c['Close'] >= rl):
                        tested_long = True
                        lvl_type = 'RL'
                        lvl_price = rl
                    elif abs(c['Low'] - fl) / fl <= 0.0025 or (c['Low'] <= fl and c['Close'] >= fl):
                        tested_long = True
                        lvl_type = 'FL'
                        lvl_price = fl

                    if tested_long:
                        entry = round(float(c['High'] + 0.01), 2)
                        stop = round(float(c['Low'] - 0.01), 2)
                        target = round(rh, 2)
                        risk = round(entry - stop, 2)
                        reward = round(target - entry, 2)

                        if risk > 0 and reward > 0:
                            rr = round(reward / risk, 2)
                            if rr >= min_planned_rr:
                                outcome = "ACTIVE_IN_PLAY"
                                exit_price = None
                                exit_time = None
                                realized_r = None

                                for j in range(i + 1, len(df_day)):
                                    sub_c = df_day.iloc[j]
                                    if sub_c['Low'] <= stop:
                                        outcome = "STOPPED_OUT"
                                        exit_price = stop
                                        exit_time = sub_c.name.strftime('%H:%M EST')
                                        realized_r = -1.0
                                        break
                                    elif sub_c['High'] >= target:
                                        outcome = "TARGET_HIT"
                                        exit_price = target
                                        exit_time = sub_c.name.strftime('%H:%M EST')
                                        realized_r = rr
                                        break

                                if outcome == "ACTIVE_IN_PLAY" and len(df_day) > i + 1:
                                    last_c = df_day.iloc[-1]
                                    exit_price = round(float(last_c['Close']), 2)
                                    exit_time = last_c.name.strftime('%H:%M EST')
                                    realized_r = round((exit_price - entry) / risk, 2) if risk > 0 else 0.0
                                    outcome = "CLOSED_AT_EOD"

                                info = meta_map.get(ticker, {})
                                setups.append({
                                    "ticker": ticker,
                                    "company_name": info.get("name", ticker),
                                    "sector": info.get("sector", "Other"),
                                    "session_date": session_date,
                                    "setup_time": t_str,
                                    "direction": "LONG",
                                    "tested_level": lvl_type,
                                    "level_price": round(lvl_price, 2),
                                    "rh": round(rh, 2),
                                    "rl": round(rl, 2),
                                    "entry_price": entry,
                                    "stop_loss": stop,
                                    "target_profit": target,
                                    "dollar_risk": risk,
                                    "dollar_reward": reward,
                                    "planned_rr": rr,
                                    "silent_pct": silent_ratio_pct,
                                    "status": outcome,
                                    "exit_price": exit_price,
                                    "exit_time": exit_time,
                                    "realized_r": realized_r
                                })
        except Exception:
            continue

    # Sort opportunities: Highest Planned R:R first
    setups.sort(key=lambda x: x['planned_rr'], reverse=True)

    mkt = get_market_status()
    primary_date = list(scanned_dates)[0] if scanned_dates else datetime.date.today().strftime('%Y-%m-%d')

    return {
        "status": "success",
        "market": mkt,
        "scanned_date": primary_date,
        "scanned_tickers_count": len(clean_tickers),
        "cached_tickers_count": len(clean_tickers) - len(tickers_to_fetch),
        "fetched_tickers_count": len(tickers_to_fetch),
        "opportunities_count": len(setups),
        "opportunities": setups
    }

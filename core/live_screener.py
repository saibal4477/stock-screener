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
    
    status_str = "OPEN (Live Market)" if is_open else ("CLOSED (Weekend)" if is_weekend else "CLOSED (After Hours / Pre-Market)")
    
    return {
        "is_open": is_open,
        "is_weekend": is_weekend,
        "current_time_est": now.strftime("%Y-%m-%d %H:%M:%S EST"),
        "status_label": status_str
    }

def scan_live_market(
    tickers: List[str],
    direction_filter: str = "SHORTS_ONLY",
    min_planned_rr: float = 1.5,
    target_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Downloads live/recent 15-minute intraday bars via Yahoo Finance API,
    establishes the 30-minute Opening Range (9:30 - 10:00 AM EST),
    and scans all subsequent candles (after 10:00 AM EST) for Silent Flip opportunities.
    """
    if not tickers:
        return {"status": "error", "message": "No tickers provided", "opportunities": []}

    meta_map = get_company_metadata()
    clean_tickers = [t.strip().upper() for t in tickers if t.strip()]

    # Download recent 5 days of 15m bars from Yahoo Finance
    try:
        data = yf.download(clean_tickers, period="5d", interval="15m", group_by="ticker", progress=False)
    except Exception as e:
        return {"status": "error", "message": f"Yahoo Finance download error: {str(e)}", "opportunities": []}

    if data is None or data.empty:
        return {"status": "error", "message": "No intraday data returned from Yahoo Finance", "opportunities": []}

    setups = []
    scanned_dates = set()

    for ticker in clean_tickers:
        try:
            if len(clean_tickers) == 1:
                df = data.dropna().copy()
            else:
                if ticker not in data.columns.levels[0]:
                    continue
                df = data[ticker].dropna().copy()

            if df.empty:
                continue

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
        "opportunities_count": len(setups),
        "opportunities": setups
    }

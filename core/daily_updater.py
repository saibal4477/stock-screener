import datetime
import os
import pytz
import time
import sqlite3
import pandas as pd
from typing import Dict, Any, Optional, List
import yfinance as yf

import config
from core.db import get_universe_tickers
from core.live_screener import get_yf_session

def update_daily_closing_data(
    universe_id: str = "scope_all",
    target_date: Optional[str] = None,
    upload_gcs: bool = True
) -> Dict[str, Any]:
    """
    Downloads today's complete 15-minute closing bars for all stocks in scope (S&P 500, Dow 30, Nasdaq 100),
    appends/updates the local SQLite database, and optionally uploads the updated database to the GCS bucket.
    Typically scheduled to run Monday-Friday at 4:30 PM EST (after regular market close at 4:00 PM).
    """
    ny_tz = pytz.timezone('America/New_York')
    now = datetime.datetime.now(ny_tz)
    
    date_str = target_date if target_date else now.strftime('%Y-%m-%d')
    date_int = int(date_str.replace('-', ''))

    # 1. Resolve Universe Tickers
    tickers = get_universe_tickers(universe_id)
    if not tickers:
        return {"status": "error", "message": "No tickers found in universe"}

    clean_tickers = [t.strip().upper() for t in tickers if t.strip()]

    # 2. Download today's bars in batches of 50
    session = get_yf_session()
    batch_size = 50
    batches = [clean_tickers[i:i + batch_size] for i in range(0, len(clean_tickers), batch_size)]
    
    rows_to_insert = []
    tickers_updated = 0

    for idx, batch in enumerate(batches):
        if idx > 0:
            time.sleep(0.35) # Polite pacing to prevent rate limits
        try:
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

            for t in batch:
                if len(batch) == 1:
                    df = b_data.dropna().copy()
                else:
                    if not (isinstance(b_data.columns, pd.MultiIndex) and t in b_data.columns.get_level_values(0)):
                        continue
                    try:
                        df = b_data[t].dropna().copy()
                    except Exception:
                        continue

                if df.empty:
                    continue

                if df.index.tz is None:
                    df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
                else:
                    df.index = df.index.tz_convert('America/New_York')

                # Filter to target date
                df_day = df[df.index.strftime('%Y-%m-%d') == date_str]
                if df_day.empty:
                    # If target date has no bars (e.g. weekend), pick the latest available session
                    avail_dates = df.index.strftime('%Y-%m-%d').unique().tolist()
                    if avail_dates:
                        latest_date = avail_dates[-1]
                        df_day = df[df.index.strftime('%Y-%m-%d') == latest_date]
                        actual_date_int = int(latest_date.replace('-', ''))
                    else:
                        continue
                else:
                    actual_date_int = date_int

                if df_day.empty:
                    continue

                tickers_updated += 1
                for idx_dt, row in df_day.iterrows():
                    time_int = int(idx_dt.strftime('%H%M%S'))
                    rows_to_insert.append((
                        t,
                        actual_date_int,
                        time_int,
                        float(row['Open']),
                        float(row['High']),
                        float(row['Low']),
                        float(row['Close']),
                        int(row.get('Volume', 0))
                    ))
        except Exception:
            continue

    if not rows_to_insert:
        return {"status": "error", "message": f"No closing bars found for date {date_str}"}

    # 3. Update SQLite Database
    db_path = config.ACTIVE_DB or config.DB_PATH_15M
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS historical_prices_15min (
            ticker TEXT,
            date INTEGER,
            time INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date, time)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hist15_ticker_date ON historical_prices_15min (ticker, date);")
    
    cur.executemany("""
        INSERT OR REPLACE INTO historical_prices_15min (ticker, date, time, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows_to_insert)
    conn.commit()
    conn.close()

    db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)

    # 4. Upload to GCS Bucket if configured
    gcs_status = "skipped"
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    if upload_gcs and bucket_name:
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob("screener_core_15min.db")
            blob.upload_from_filename(db_path)
            gcs_status = f"uploaded to gs://{bucket_name}/screener_core_15min.db ({db_size_mb} MB)"
        except Exception as e:
            gcs_status = f"failed: {str(e)}"

    return {
        "status": "success",
        "date_updated": date_str,
        "tickers_updated": tickers_updated,
        "total_bars_inserted": len(rows_to_insert),
        "db_path": db_path,
        "db_size_mb": db_size_mb,
        "gcs_status": gcs_status
    }

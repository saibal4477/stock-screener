# ==============================================================================
# Model Context Protocol (MCP) Unified Stock Screener Server
# ==============================================================================
# This single-file server provides a 100% database-driven framework for:
#   1. Bulk importing/updating price history from Stooq ZIP data.
#   2. Calculating advanced technical indicators in parallel (SMAs, EMAs, RSI, MACD).
#   3. Executing Strategy DSL screens based on custom conditions (strategies.json).
#   4. Dynamic support/resistance level clustering (Swing Highs/Lows).
#   5. Generating premium dark-themed candlestick charts with technical overlays.
#
# Database Schema (screener.db):
#   - historical_prices (ticker, date, open, high, low, close, volume) [WITHOUT ROWID]
#   - stocks (ticker, exchange, type, price, indicators..., filepath)
#   - company_info (ticker, sector, industry, market_cap)
#   - etf_holdings (etf_ticker, stock_ticker, weight)
# ==============================================================================

import os
import sqlite3
import pandas as pd
from fastmcp import FastMCP

# Ensure matplotlib runs headlessly since MCP servers execute as background processes
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Initialize FastMCP server
mcp = FastMCP("Stock Screener Server")

DB_PATH = "screener.db"

# Centralized Config Paths for consolidated S/R and trading rules

ARTIFACTS_DIR = r"C:\Users\saiba\.gemini\antigravity\brain\e45b717b-e889-4495-a9ea-8c882f2d80f8"
RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_rules.json")
# Additional Constants for data extraction
EXTRACT_DIR = r"C:\stock-data"

# --- Inlined update_db.py logic ---


def get_last_line_date(ticker):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) FROM historical_prices WHERE ticker = ?", (ticker.upper(),))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return None

def update_from_file(update_filepath, file_map=None):
    import sqlite3
    if not os.path.exists(update_filepath):
        print(f"Error: Update file {update_filepath} does not exist.")
        return False
        
    print(f"Processing update file: {update_filepath}...")
    appended_count = 0
    skipped_count = 0
    db_records = []
    
    with open(update_filepath, "r") as f:
        header = f.readline() # Read header
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            parts = line_str.split(",")
            if len(parts) < 9:
                continue
                
            ticker = parts[0].upper()
            if not ticker.endswith(".US"):
                continue # Skip non-US tickers
                
            clean_ticker = ticker.split('.')[0].upper()
            new_date = parts[2]
            
            last_date = get_last_line_date(clean_ticker)
            
            if last_date is None or new_date > last_date:
                # Prepare for DB insert
                db_records.append((
                    clean_ticker,
                    int(parts[2]),
                    float(parts[4]),
                    float(parts[5]),
                    float(parts[6]),
                    float(parts[7]),
                    int(float(parts[8]))
                ))
                appended_count += 1
            else:
                skipped_count += 1
                
    if db_records:
        print(f"Writing {len(db_records)} records to historical_prices table in database...")
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("PRAGMA synchronous = OFF")
            cursor.execute("PRAGMA journal_mode = MEMORY")
            insert_sql = "INSERT OR REPLACE INTO historical_prices (ticker, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)"
            cursor.executemany(insert_sql, db_records)
            conn.commit()
            conn.close()
            print("Database historical_prices table updated.")
        except Exception as e:
            print(f"Error updating historical_prices table: {e}")
            
    print(f"Update completed:")
    print(f"  - Loaded records: {appended_count}")
    print(f"  - Skipped (already exists): {skipped_count}")
    return True


# --- Inlined import_stooq_zip.py & indexer.py logic ---
def extract_zip(zip_path, extract_dir):
    import zipfile
    import time
    print(f"Unzipping {zip_path} to {extract_dir}...")
    start = time.time()
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
    print(f"Unzip completed in {time.time() - start:.2f} seconds.")

def parse_file(filepath):
    import os
    filename = os.path.basename(filepath)
    ticker = filename.split('.')[0].upper()
    
    records = []
    try:
        with open(filepath, 'r') as f:
            header = f.readline()  # Skip header line
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 9:
                    # date at index 2, open/high/low/close at 4-7, volume at index 8
                    records.append((
                        ticker,
                        int(parts[2]),
                        float(parts[4]),
                        float(parts[5]),
                        float(parts[6]),
                        float(parts[7]),
                        int(float(parts[8]))
                    ))
    except Exception:
        pass
    return records

def load_historical_prices():
    import time
    from concurrent.futures import ProcessPoolExecutor
    start_time = time.time()
    
    search_dir = os.path.join(EXTRACT_DIR, "data", "daily", "us")
    if not os.path.exists(search_dir):
        print(f"Error: Stooq daily folder not found at {search_dir}")
        return
        
    print("Scanning for historical files...")
    filepaths = []
    for root, _, files in os.walk(search_dir):
        for f in files:
            if f.endswith(".txt"):
                filepaths.append(os.path.join(root, f))
                
    total_files = len(filepaths)
    print(f"Found {total_files} text files to process.")
    
    # Extract metadata map (ticker -> exchange, type) from path info
    print("Extracting ticker metadata...")
    ticker_metadata = []
    for filepath in filepaths:
        ticker, exchange, asset_type = parse_path_info(filepath)
        ticker_metadata.append((ticker, exchange, asset_type))
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Insert metadata into stocks table first (initial metadata bootstrap)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            exchange TEXT,
            type TEXT,
            date TEXT,
            price REAL,
            change_percent REAL,
            volume REAL,
            avg_volume_20 REAL,
            avg_volume_50 REAL,
            sma_20 REAL,
            sma_50 REAL,
            sma_200 REAL,
            ema_9 REAL,
            ema_20 REAL,
            ema_12 REAL,
            ema_26 REAL,
            ema_50 REAL,
            rsi_14 REAL,
            macd REAL,
            macd_signal REAL,
            macd_hist REAL,
            high_52w REAL,
            low_52w REAL,
            dist_from_high_52w_pct REAL,
            dist_from_low_52w_pct REAL,
            return_1d REAL,
            return_5d REAL,
            return_20d REAL,
            return_250d REAL,
            volatility_20 REAL,
            filepath TEXT
        )
    """)
    for ticker, exchange, asset_type in ticker_metadata:
        cursor.execute("INSERT OR IGNORE INTO stocks (ticker, exchange, type) VALUES (?, ?, ?)", (ticker, exchange, asset_type))
    conn.commit()
    
    # Enable performance options for bulk historical prices insertion
    cursor.execute("PRAGMA synchronous = OFF")
    cursor.execute("PRAGMA journal_mode = MEMORY")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historical_prices (
            ticker TEXT,
            date INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        ) WITHOUT ROWID
    """)
    conn.commit()
    
    print("Parsing files in parallel...")
    batch = []
    batch_size = 500000
    total_inserted = 0
    
    insert_sql = "INSERT OR REPLACE INTO historical_prices (ticker, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)"
    
    with ProcessPoolExecutor() as executor:
        chunksize = max(1, total_files // 100)
        jobs = executor.map(parse_file, filepaths, chunksize=chunksize)
        
        for file_records in jobs:
            if file_records:
                batch.extend(file_records)
                
            if len(batch) >= batch_size:
                cursor.executemany(insert_sql, batch)
                conn.commit()
                total_inserted += len(batch)
                print(f"Inserted {total_inserted} price records...")
                batch = []
                
        if batch:
            cursor.executemany(insert_sql, batch)
            conn.commit()
            total_inserted += len(batch)
            print(f"Inserted {total_inserted} price records...")
            
    print("Creating database index on historical_prices(ticker, date)...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hist_ticker_date ON historical_prices(ticker, date DESC)")
    conn.commit()
    conn.close()
    print(f"Successfully loaded {total_inserted} daily historical rows into SQLite database in {time.time() - start_time:.2f} seconds.")


# --- Inlined indexer.py logic ---
def parse_path_info(filepath):
    parts = filepath.lower().split(os.sep)
    exchange = "UNKNOWN"
    asset_type = "UNKNOWN"
    for part in parts:
        if "nasdaq" in part:
            exchange = "NASDAQ"
            asset_type = "etf" if "etf" in part else "stock"
        elif "nyse" in part:
            if "nysemkt" in part:
                exchange = "NYSEMKT"
            else:
                exchange = "NYSE"
            asset_type = "etf" if "etf" in part else "stock"
            
    filename = os.path.basename(filepath)
    ticker = filename.split('.')[0].upper()
    return ticker, exchange, asset_type


def parse_file_5min(filepath):
    filename = os.path.basename(filepath)
    ticker = filename.split('.')[0].upper()
    if ticker.endswith(".US"):
        ticker = ticker[:-3]
    
    records = []
    try:
        with open(filepath, 'r') as f:
            header = f.readline()  # Skip header line
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 9:
                    records.append((
                        ticker,
                        int(parts[2]),
                        int(parts[3]),
                        float(parts[4]),
                        float(parts[5]),
                        float(parts[6]),
                        float(parts[7]),
                        int(float(parts[8]))
                    ))
    except Exception:
        pass
    return records


def parse_path_info_5min(filepath):
    parts = filepath.lower().replace('/', os.sep).replace('\\', os.sep).split(os.sep)
    exchange = "UNKNOWN"
    asset_type = "UNKNOWN"
    for part in parts:
        if "nasdaq" in part:
            exchange = "NASDAQ"
            asset_type = "etf" if "etf" in part else "stock"
        elif "nyse" in part:
            if "nysemkt" in part:
                exchange = "NYSEMKT"
            else:
                exchange = "NYSE"
            asset_type = "etf" if "etf" in part else "stock"
            
    filename = os.path.basename(filepath)
    ticker = filename.split('.')[0].upper()
    if ticker.endswith(".US"):
        ticker = ticker[:-3]
    return ticker, exchange, asset_type


def stream_load_historical_prices_5min(zip_path):
    import zipfile
    import time
    start_time = time.time()
    
    z = zipfile.ZipFile(zip_path, 'r')
    txt_files = [f for f in z.namelist() if f.endswith('.txt') and 'data/5 min/us' in f.lower().replace('\\', '/')]
    if not txt_files:
        txt_files = [f for f in z.namelist() if f.endswith('.txt')]
        
    total_files = len(txt_files)
    if total_files == 0:
        z.close()
        return False
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA synchronous = OFF")
    cursor.execute("PRAGMA journal_mode = MEMORY")
    cursor.execute("PRAGMA cache_size = -100000")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historical_prices_5min (
            ticker TEXT,
            date INTEGER,
            time INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date, time)
        ) WITHOUT ROWID
    """)
    conn.commit()
    
    ticker_metadata = []
    for filepath in txt_files:
        ticker, exchange, asset_type = parse_path_info_5min(filepath)
        ticker_metadata.append((ticker, exchange, asset_type))
        
    cursor.executemany(
        "INSERT OR IGNORE INTO stocks (ticker, exchange, type) VALUES (?, ?, ?)", 
        ticker_metadata
    )
    conn.commit()
    
    insert_sql = "INSERT OR REPLACE INTO historical_prices_5min (ticker, date, time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    batch = []
    batch_size = 500000
    total_inserted = 0
    
    for filepath in txt_files:
        filename = filepath.replace('\\', '/').split('/')[-1]
        ticker = filename.split('.')[0].upper()
        if ticker.endswith(".US"):
            ticker = ticker[:-3]
        try:
            raw_bytes = z.read(filepath)
            content = raw_bytes.decode('ascii', errors='ignore')
            lines = content.splitlines()
            for line in lines[1:]:
                parts = line.split(',')
                if len(parts) >= 9:
                    batch.append((
                        ticker,
                        int(parts[2]),
                        int(parts[3]),
                        float(parts[4]),
                        float(parts[5]),
                        float(parts[6]),
                        float(parts[7]),
                        int(float(parts[8]))
                    ))
        except Exception:
            pass
            
        if len(batch) >= batch_size:
            cursor.executemany(insert_sql, batch)
            conn.commit()
            total_inserted += len(batch)
            batch = []
            
    if batch:
        cursor.executemany(insert_sql, batch)
        conn.commit()
        total_inserted += len(batch)
        batch = []
        
    z.close()
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hist_5min_ticker_date_time ON historical_prices_5min(ticker, date DESC, time DESC)")
    conn.commit()
    conn.close()
    print(f"Successfully loaded {total_inserted:,} 5-minute historical rows into SQLite database in {time.time() - start_time:.2f} seconds.")
    return total_inserted



def calculate_stock_beta_worker(ticker, spy_ret):
    try:
        df = load_history_df(ticker, 300)
        if df is None or len(df) < 30:
            return ticker, None
            
        df = df.sort_values('date').reset_index(drop=True)
        df['return'] = df['close'].pct_change()
        
        stock_ret = df.set_index('date')['return'].dropna().tail(250)
        merged = pd.merge(stock_ret, spy_ret, left_index=True, right_index=True, suffixes=('_stock', '_spy'))
        if len(merged) < 30:
            return ticker, None
            
        cov = merged['return_stock'].cov(merged['return_spy'])
        var = merged['return_spy'].var()
        
        if var == 0 or pd.isna(cov) or pd.isna(var):
            return ticker, None
            
        beta = cov / var
        return ticker, round(float(beta), 3)
    except Exception:
        return ticker, None


def load_history_df(ticker, n_days=350):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, open, high, low, close, volume
            FROM historical_prices
            WHERE ticker = ?
            ORDER BY date ASC
        """, (ticker.upper(),))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return None
            
        df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
        df['date'] = pd.to_datetime(df['date'].astype(str), format='%Y%m%d')
        df = df.tail(n_days).copy().reset_index(drop=True)
        return df
    except Exception:
        return None


def load_history_df_5min(ticker, limit=100):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, time, open, high, low, close, volume
            FROM historical_prices_5min
            WHERE ticker = ?
            ORDER BY date DESC, time DESC
            LIMIT ?
        """, (ticker.upper(), limit))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return None
            
        rows.reverse()
        
        df = pd.DataFrame(rows, columns=['date', 'time', 'open', 'high', 'low', 'close', 'vol'])
        def to_dt(row):
            dt_str = f"{int(row['date'])}{int(row['time']):06d}"
            return pd.to_datetime(dt_str, format='%Y%m%d%H%M%S')
        df['datetime'] = df.apply(to_dt, axis=1)
        return df
    except Exception:
        return None


def read_last_lines_db(ticker, n=350):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, open, high, low, close, volume
            FROM historical_prices
            WHERE ticker = ?
            ORDER BY date ASC
        """, (ticker,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return None
            
        df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
        df['date'] = df['date'].astype(str)
        df = df.tail(n).copy().reset_index(drop=True)
        return df
    except Exception:
        return None

def process_single_ticker(ticker_info):
    ticker, exchange, asset_type = ticker_info
    df = read_last_lines_db(ticker, n=350)
    
    if df is None or len(df) < 2:
        return None
        
    try:
        df = df.sort_values('date').reset_index(drop=True)
        close = df['close']
        vol = df['vol']
        
        metrics = {
            'ticker': ticker,
            'exchange': exchange,
            'type': asset_type,
            'date': str(df['date'].iloc[-1]),
            'price': float(close.iloc[-1]),
            'volume': float(vol.iloc[-1]),
            'filepath': f"DATABASE://{ticker}"
        }
        
        if len(df) >= 2:
            metrics['change_percent'] = float(((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100)
            metrics['return_1d'] = metrics['change_percent']
        else:
            metrics['change_percent'] = 0.0
            metrics['return_1d'] = 0.0
            
        metrics['return_5d'] = float(((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6]) * 100) if len(df) >= 6 else None
        metrics['return_20d'] = float(((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]) * 100) if len(df) >= 21 else None
        metrics['return_250d'] = float(((close.iloc[-1] - close.iloc[-251]) / close.iloc[-251]) * 100) if len(df) >= 251 else None
        
        metrics['avg_volume_20'] = float(vol.rolling(20).mean().iloc[-1]) if len(df) >= 20 else None
        metrics['avg_volume_50'] = float(vol.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
        
        metrics['sma_20'] = float(close.rolling(20).mean().iloc[-1]) if len(df) >= 20 else None
        metrics['sma_50'] = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
        metrics['sma_200'] = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
        
        metrics['ema_9'] = float(close.ewm(span=9, adjust=False).mean().iloc[-1]) if len(df) >= 9 else None
        metrics['ema_20'] = float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(df) >= 20 else None
        metrics['ema_12'] = float(close.ewm(span=12, adjust=False).mean().iloc[-1]) if len(df) >= 12 else None
        metrics['ema_26'] = float(close.ewm(span=26, adjust=False).mean().iloc[-1]) if len(df) >= 26 else None
        metrics['ema_50'] = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(df) >= 50 else None
        
        if len(df) >= 15:
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi = 100 - (100 / (1 + rs))
            metrics['rsi_14'] = float(rsi.iloc[-1])
        else:
            metrics['rsi_14'] = None
            
        if len(df) >= 26:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            hist = macd - signal
            metrics['macd'] = float(macd.iloc[-1])
            metrics['macd_signal'] = float(signal.iloc[-1])
            metrics['macd_hist'] = float(hist.iloc[-1])
        else:
            metrics['macd'] = None
            metrics['macd_signal'] = None
            metrics['macd_hist'] = None
            
        window_52w = min(len(df), 252)
        recent_df = df.iloc[-window_52w:]
        metrics['high_52w'] = float(recent_df['high'].max())
        metrics['low_52w'] = float(recent_df['low'].min())
        
        if metrics['high_52w'] > 0:
            metrics['dist_from_high_52w_pct'] = float(((metrics['price'] - metrics['high_52w']) / metrics['high_52w']) * 100)
        else:
            metrics['dist_from_high_52w_pct'] = 0.0
            
        if metrics['low_52w'] > 0:
            metrics['dist_from_low_52w_pct'] = float(((metrics['price'] - metrics['low_52w']) / metrics['low_52w']) * 100)
        else:
            metrics['dist_from_low_52w_pct'] = 0.0
            
        if len(df) >= 21:
            daily_returns = close.pct_change()
            metrics['volatility_20'] = float(daily_returns.rolling(20).std().iloc[-1] * 100)
        else:
            metrics['volatility_20'] = None
            
        return metrics
    except Exception:
        return None
        
    try:
        df = df.sort_values('date').reset_index(drop=True)
        close = df['close']
        vol = df['vol']
        
        metrics = {
            'ticker': ticker,
            'exchange': exchange,
            'type': asset_type,
            'date': str(df['date'].iloc[-1]),
            'price': float(close.iloc[-1]),
            'volume': float(vol.iloc[-1]),
            'filepath': filepath
        }
        
        if len(df) >= 2:
            metrics['change_percent'] = float(((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100)
            metrics['return_1d'] = metrics['change_percent']
        else:
            metrics['change_percent'] = 0.0
            metrics['return_1d'] = 0.0
            
        metrics['return_5d'] = float(((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6]) * 100) if len(df) >= 6 else None
        metrics['return_20d'] = float(((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]) * 100) if len(df) >= 21 else None
        metrics['return_250d'] = float(((close.iloc[-1] - close.iloc[-251]) / close.iloc[-251]) * 100) if len(df) >= 251 else None
        
        metrics['avg_volume_20'] = float(vol.rolling(20).mean().iloc[-1]) if len(df) >= 20 else None
        metrics['avg_volume_50'] = float(vol.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
        
        metrics['sma_20'] = float(close.rolling(20).mean().iloc[-1]) if len(df) >= 20 else None
        metrics['sma_50'] = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
        metrics['sma_200'] = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
        
        metrics['ema_9'] = float(close.ewm(span=9, adjust=False).mean().iloc[-1]) if len(df) >= 9 else None
        metrics['ema_20'] = float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(df) >= 20 else None
        metrics['ema_12'] = float(close.ewm(span=12, adjust=False).mean().iloc[-1]) if len(df) >= 12 else None
        metrics['ema_26'] = float(close.ewm(span=26, adjust=False).mean().iloc[-1]) if len(df) >= 26 else None
        metrics['ema_50'] = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(df) >= 50 else None
        
        if len(df) >= 15:
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi = 100 - (100 / (1 + rs))
            metrics['rsi_14'] = float(rsi.iloc[-1])
        else:
            metrics['rsi_14'] = None
            
        if len(df) >= 26:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            hist = macd - signal
            metrics['macd'] = float(macd.iloc[-1])
            metrics['macd_signal'] = float(signal.iloc[-1])
            metrics['macd_hist'] = float(hist.iloc[-1])
        else:
            metrics['macd'] = None
            metrics['macd_signal'] = None
            metrics['macd_hist'] = None
            
        window_52w = min(len(df), 252)
        recent_df = df.iloc[-window_52w:]
        metrics['high_52w'] = float(recent_df['high'].max())
        metrics['low_52w'] = float(recent_df['low'].min())
        
        if metrics['high_52w'] > 0:
            metrics['dist_from_high_52w_pct'] = float(((metrics['price'] - metrics['high_52w']) / metrics['high_52w']) * 100)
        else:
            metrics['dist_from_high_52w_pct'] = 0.0
            
        if metrics['low_52w'] > 0:
            metrics['dist_from_low_52w_pct'] = float(((metrics['price'] - metrics['low_52w']) / metrics['low_52w']) * 100)
        else:
            metrics['dist_from_low_52w_pct'] = 0.0
            
        if len(df) >= 21:
            daily_returns = close.pct_change()
            metrics['volatility_20'] = float(daily_returns.rolling(20).std().iloc[-1] * 100)
        else:
            metrics['volatility_20'] = None
            
        return metrics
    except Exception:
        return None

def create_db_schema(conn):
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS stocks")
    cursor.execute("""
    CREATE TABLE stocks (
        ticker TEXT PRIMARY KEY,
        exchange TEXT,
        type TEXT,
        date TEXT,
        price REAL,
        change_percent REAL,
        volume REAL,
        avg_volume_20 REAL,
        avg_volume_50 REAL,
        sma_20 REAL,
        sma_50 REAL,
        sma_200 REAL,
        ema_9 REAL,
        ema_20 REAL,
        ema_12 REAL,
        ema_26 REAL,
        ema_50 REAL,
        rsi_14 REAL,
        macd REAL,
        macd_signal REAL,
        macd_hist REAL,
        high_52w REAL,
        low_52w REAL,
        dist_from_high_52w_pct REAL,
        dist_from_low_52w_pct REAL,
        return_1d REAL,
        return_5d REAL,
        return_20d REAL,
        return_250d REAL,
        volatility_20 REAL,
        filepath TEXT
    )
    """)
    conn.commit()

def run_indexing():
    import time
    from concurrent.futures import ProcessPoolExecutor
    start_time = time.time()
    
    print("Fetching tickers list from database historical_prices...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if we have previous exchange metadata in stocks table
    cursor.execute("SELECT ticker, exchange, type FROM stocks")
    metadata_map = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    
    cursor.execute("SELECT DISTINCT ticker FROM historical_prices")
    db_tickers = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    total_tickers = len(db_tickers)
    print(f"Found {total_tickers} tickers in database to process.")
    
    # Prepare list of tuples: (ticker, exchange, asset_type)
    ticker_infos = []
    for ticker in db_tickers:
        meta = metadata_map.get(ticker, ("NASDAQ", "stock"))  # Default exchange to NASDAQ, type to stock
        ticker_infos.append((ticker, meta[0], meta[1]))
        
    print("Processing tickers and calculating indicators using multiprocessing...")
    results = []
    
    with ProcessPoolExecutor() as executor:
        chunksize = max(1, total_tickers // 100)
        jobs = executor.map(process_single_ticker, ticker_infos, chunksize=chunksize)
        
        count = 0
        for res in jobs:
            count += 1
            if res is not None:
                results.append(res)
            if count % 1000 == 0:
                print(f"Processed {count}/{total_tickers} tickers...")
                
    print(f"Successfully processed {len(results)} active stocks/ETFs.")
    
    print(f"Writing to SQLite database at {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    create_db_schema(conn)
    
    cursor = conn.cursor()
    insert_sql = """
    INSERT OR REPLACE INTO stocks (
        ticker, exchange, type, date, price, change_percent, volume,
        avg_volume_20, avg_volume_50, sma_20, sma_50, sma_200,
        ema_9, ema_20, ema_12, ema_26, ema_50, rsi_14, macd, macd_signal, macd_hist,
        high_52w, low_52w, dist_from_high_52w_pct, dist_from_low_52w_pct,
        return_1d, return_5d, return_20d, return_250d, volatility_20, filepath
    ) VALUES (
        :ticker, :exchange, :type, :date, :price, :change_percent, :volume,
        :avg_volume_20, :avg_volume_50, :sma_20, :sma_50, :sma_200,
        :ema_9, :ema_20, :ema_12, :ema_26, :ema_50, :rsi_14, :macd, :macd_signal, :macd_hist,
        :high_52w, :low_52w, :dist_from_high_52w_pct, :dist_from_low_52w_pct,
        :return_1d, :return_5d, :return_20d, :return_250d, :volatility_20, :filepath
    )
    """
    cursor.executemany(insert_sql, results)
    conn.commit()
    conn.close()
    
    end_time = time.time()
    print(f"Indexing completed in {end_time - start_time:.2f} seconds!")


# --- Consolidated trading_rules.py ---
def load_rules():
    default_rules = {
        "portfolio_size": 50000.0,
        "max_allocation_pct": 5.0,
        "max_risk_pct": 1.0,
        "min_risk_reward_ratio": 3.0,
        "min_atr_multiple_sl": 2.0
    }
    if not os.path.exists(RULES_FILE):
        return default_rules
    try:
        import json
        with open(RULES_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading trading rules: {e}")
        return default_rules

def evaluate_trade(entry, sl, target):
    rules = load_rules()
    risk_per_share = abs(entry - sl)
    if risk_per_share <= 0:
        return None
    reward_per_share = abs(target - entry)
    rr_ratio = reward_per_share / risk_per_share
    if rr_ratio < rules["min_risk_reward_ratio"]:
        return None
    portfolio = rules["portfolio_size"]
    max_alloc = portfolio * (rules["max_allocation_pct"] / 100.0)
    max_risk = portfolio * (rules["max_risk_pct"] / 100.0)
    shares_alloc = int(max_alloc // entry)
    shares_risk = int(max_risk // risk_per_share)
    shares = min(shares_alloc, shares_risk)
    if shares <= 0:
        return None
    return {
        "shares": shares,
        "total_alloc": float(shares * entry),
        "total_risk": float(shares * risk_per_share),
        "rr_ratio": float(rr_ratio),
        "risk_per_share": float(risk_per_share),
        "reward_per_share": float(reward_per_share)
    }

# --- Consolidated detect_sr.py ---


def detect_levels(df, window=5, threshold_pct=0.015):
    import numpy as np
    lows = df['low'].values
    highs = df['high'].values
    troughs = []
    peaks = []
    for i in range(window, len(df) - window):
        if lows[i] == min(lows[i-window:i+window+1]):
            troughs.append(lows[i])
        if highs[i] == max(highs[i-window:i+window+1]):
            peaks.append(highs[i])
    if len(df) >= 10:
        troughs.append(min(lows[-3:]))
        peaks.append(max(highs[-3:]))
    supports = []
    for p in sorted(troughs):
        close_pts = [x for x in troughs if abs(x - p) / p <= threshold_pct]
        if len(close_pts) >= 2:
            avg_p = float(np.mean(close_pts))
            if not any(abs(avg_p - s) / s <= threshold_pct for s in supports):
                supports.append(avg_p)
    resistances = []
    for p in sorted(peaks):
        close_pts = [x for x in peaks if abs(x - p) / p <= threshold_pct]
        if len(close_pts) >= 2:
            avg_p = float(np.mean(close_pts))
            if not any(abs(avg_p - r) / r <= threshold_pct for r in resistances):
                resistances.append(avg_p)
    if not supports and troughs:
        supports = sorted(troughs)
    if not resistances and peaks:
        resistances = sorted(peaks)
    supports = sorted(supports)
    filtered_supports = []
    for s in supports:
        if not filtered_supports or (s - filtered_supports[-1]) / filtered_supports[-1] > 0.03:
            filtered_supports.append(s)
    resistances = sorted(resistances)
    filtered_resistances = []
    for r in resistances:
        if not filtered_resistances or (r - filtered_resistances[-1]) / filtered_resistances[-1] > 0.03:
            filtered_resistances.append(r)
    return filtered_supports, filtered_resistances

def draw_trade_chart_with_sr(ticker, entry, sl, target, timeframe_label, moving_average="9ema"):
    try:
        import matplotlib.dates as mdates
        df = load_history_df(ticker, 260)
        if df is None or len(df) < 20:
            print(f"Error: no historical data found for {ticker}")
            return False
            
        # Calculate MAs
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['sma_50'] = df['close'].rolling(50).mean()
        df['sma_200'] = df['close'].rolling(200).mean()
        
        # Calculate Bollinger Bands (20-day, 2 std dev)
        df['bb_mid'] = df['close'].rolling(20).mean()
        df['bb_std'] = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
        df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
        
        # Determine the number of days for the zoom-in trade chart
        days_zoom = 252 if timeframe_label == "1-Year" else (60 if timeframe_label == "3-Month" else 20)
        
        # ----------------------------------------------------
        # Chart 1: 1-Year Context Chart (Always generated)
        # ----------------------------------------------------
        plt.style.use('dark_background')
        fig1, ax1 = plt.subplots(figsize=(12, 6))
        fig1.suptitle(f'{ticker} - 1-Year Full Context Chart (with 50/200 SMAs & Bollinger Bands)', fontsize=14, color='#58a6ff', weight='bold')
        
        # Plot candles
        ax1.vlines(df['date'], df['low'], df['high'], color='#8b949e', linewidth=1, zorder=2)
        up_days_full = df[df['close'] >= df['open']]
        down_days_full = df[df['close'] < df['open']]
        ax1.bar(up_days_full['date'], up_days_full['close'] - up_days_full['open'], bottom=up_days_full['open'], color='#2ea44f', width=0.6, edgecolor='#2ea44f', zorder=3)
        ax1.bar(down_days_full['date'], down_days_full['open'] - down_days_full['close'], bottom=down_days_full['close'], color='#d73a49', width=0.6, edgecolor='#d73a49', zorder=3)
        
        # Plot SMAs
        ax1.plot(df['date'], df['sma_50'], label='50-day SMA', color='#1f77b4', linestyle='--', linewidth=1.2, zorder=4)
        ax1.plot(df['date'], df['sma_200'], label='200-day SMA', color='#d62728', linestyle='--', linewidth=1.2, zorder=4)
        
        # Plot Bollinger Bands
        ax1.plot(df['date'], df['bb_upper'], label='BB Upper (20, 2)', color='#8b949e', linestyle=':', linewidth=0.8, alpha=0.7, zorder=4)
        ax1.plot(df['date'], df['bb_lower'], label='BB Lower (20, 2)', color='#8b949e', linestyle=':', linewidth=0.8, alpha=0.7, zorder=4)
        ax1.fill_between(df['date'], df['bb_lower'], df['bb_upper'], color='#8b949e', alpha=0.04, zorder=1)
        
        ax1.set_ylabel('Price ($)', fontsize=12, color='#8b949e')
        ax1.grid(True, color='#21262d', linestyle=':', alpha=0.5)
        ax1.tick_params(axis='both', colors='#8b949e')
        ax1.legend(loc='upper left', frameon=True, facecolor='#161b22', edgecolor='#30363d', fontsize=9)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        fig1.autofmt_xdate()
        plt.tight_layout()
        
        context_path = os.path.join(ARTIFACTS_DIR, f"{ticker}_1y_context.png")
        plt.savefig(context_path, dpi=150, facecolor='#0d1117')
        plt.close()
        
        # ----------------------------------------------------
        # Chart 2: Zoomed-In Trade execution chart
        # ----------------------------------------------------
        df_zoom = df.tail(days_zoom).copy()
        
        # Get S/R levels based on the specific timeframe used
        supports, resistances = detect_levels(df_zoom, window=3 if days_zoom==60 else (2 if days_zoom==20 else 5), threshold_pct=0.015)
        
        fig2, (ax2, ax_rsi) = plt.subplots(2, 1, figsize=(12, 8.5), gridspec_kw={'height_ratios': [3.2, 1]}, sharex=True)
        fig2.suptitle(f'{ticker} - Pullback Trade Setup ({timeframe_label} Zoom)', fontsize=16, color='#58a6ff', weight='bold')
        
        # Plot zoom candles
        ax2.vlines(df_zoom['date'], df_zoom['low'], df_zoom['high'], color='#8b949e', linewidth=1.2, zorder=2)
        up_days_zoom = df_zoom[df_zoom['close'] >= df_zoom['open']]
        down_days_zoom = df_zoom[df_zoom['close'] < df_zoom['open']]
        ax2.bar(up_days_zoom['date'], up_days_zoom['close'] - up_days_zoom['open'], bottom=up_days_zoom['open'], color='#2ea44f', width=0.6, edgecolor='#2ea44f', zorder=3, label='Bullish Day')
        ax2.bar(down_days_zoom['date'], down_days_zoom['open'] - down_days_zoom['close'], bottom=down_days_zoom['close'], color='#d73a49', width=0.6, edgecolor='#d73a49', zorder=3, label='Bearish Day')
        
        # Plot selected moving average
        ma_label = ""
        ma_color = "#ff7f0e"
        if moving_average == "9ema":
            df_zoom['ma_plot'] = df_zoom['ema_9']
            ma_label = "9-day EMA"
        elif moving_average == "20ema":
            df_zoom['ma_plot'] = df_zoom['ema_20']
            ma_label = "20-day EMA"
            ma_color = "#9467bd"
        elif moving_average == "50sma":
            df_zoom['ma_plot'] = df_zoom['sma_50']
            ma_label = "50-day SMA"
            ma_color = "#1f77b4"
        elif moving_average == "200sma":
            df_zoom['ma_plot'] = df_zoom['sma_200']
            ma_label = "200-day SMA"
            ma_color = "#d62728"
        elif moving_average == "bollinger_lower":
            if 'bollinger_mid' not in df_zoom.columns:
                df_zoom['bollinger_mid'] = df_zoom['close'].rolling(20).mean()
                df_zoom['bollinger_std'] = df_zoom['close'].rolling(20).std()
                df_zoom['bollinger_upper'] = df_zoom['bollinger_mid'] + 2 * df_zoom['bollinger_std']
                df_zoom['bollinger_lower'] = df_zoom['bollinger_mid'] - 2 * df_zoom['bollinger_std']
            df_zoom['ma_plot'] = df_zoom['bollinger_mid']
            ma_label = "Bollinger Mid (20 SMA)"
            ma_color = "#9467bd"
            ax2.plot(df_zoom['date'], df_zoom['bollinger_upper'], color='#58a6ff', linestyle='--', linewidth=1.0, alpha=0.6, label='Bollinger Upper')
            ax2.plot(df_zoom['date'], df_zoom['bollinger_lower'], color='#58a6ff', linestyle='--', linewidth=1.0, alpha=0.6, label='Bollinger Lower')
        else:
            df_zoom['ma_plot'] = df_zoom['ema_9']
            ma_label = "9-day EMA"
            ma_color = "#ff7f0e"
            
        ax2.plot(df_zoom['date'], df_zoom['ma_plot'], label=ma_label, color=ma_color, linestyle='-', linewidth=1.5, zorder=4)
        
        # Plot S/R lines
        dates = df_zoom['date'].values
        start_date = dates[0]
        end_date = dates[-1]
        y_min, y_max = df_zoom['low'].min(), df_zoom['high'].max()
        
        for sup in supports:
            if y_min <= sup <= y_max:
                ax2.hlines(sup, start_date, end_date, colors='#2ea44f', linestyles=':', linewidth=1.2, zorder=4, alpha=0.6)
                ax2.text(start_date, sup, f' Support: ${sup:.2f}', color='#2ea44f', va='bottom', ha='left', fontsize=8, alpha=0.7)
                
        for res in resistances:
            if y_min <= res <= y_max:
                ax2.hlines(res, start_date, end_date, colors='#f85149', linestyles=':', linewidth=1.2, zorder=4, alpha=0.6)
                ax2.text(start_date, res, f' Resistance: ${res:.2f}', color='#f85149', va='bottom', ha='left', fontsize=8, alpha=0.7)
                
        # Draw Trade Levels
        ax2.hlines(entry, start_date, end_date, colors='#238636', linestyles='--', linewidth=2.2, zorder=5, label=f'Entry (Current Close): ${entry:.2f}')
        ax2.text(end_date, entry, f'  Entry: ${entry:.2f}', color='#2ea44f', va='center', weight='bold', fontsize=10)
        
        ax2.hlines(sl, start_date, end_date, colors='#da3637', linestyles='--', linewidth=2.2, zorder=5, label=f'Stop Loss (S/R-based): ${sl:.2f}')
        ax2.text(end_date, sl, f'  Stop Loss: ${sl:.2f}', color='#f85149', va='center', weight='bold', fontsize=10)
        
        ax2.hlines(target, start_date, end_date, colors='#1f6feb', linestyles='--', linewidth=2.2, zorder=5, label=f'Target (Exit): ${target:.2f}')
        ax2.text(end_date, target, f'  Target: ${target:.2f}', color='#58a6ff', va='center', weight='bold', fontsize=10)
        
        ax2.set_ylabel('Price ($)', fontsize=12, color='#8b949e')
        ax2.legend(loc='upper left', frameon=True, facecolor='#161b22', edgecolor='#30363d')
        ax2.grid(True, color='#21262d', linestyle=':', alpha=0.6)
        ax2.tick_params(axis='both', colors='#8b949e')
        
        # Plot RSI-14
        if 'rsi_14' not in df_zoom.columns:
            close_series = df['close']
            delta = close_series.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.ewm(com=13, adjust=False).mean()
            avg_loss = loss.ewm(com=13, adjust=False).mean()
            rs = avg_gain / (avg_loss + 1e-10)
            df['rsi_14'] = 100.0 - (100.0 / (1.0 + rs))
            df_zoom = df.tail(days_zoom).copy()
            
        ax_rsi.plot(df_zoom['date'], df_zoom['rsi_14'], color='#ff7f0e', linewidth=1.5, label='RSI (14)')
        ax_rsi.axhline(70, color='#da3637', linestyle='--', linewidth=0.8, alpha=0.7)
        ax_rsi.axhline(30, color='#2ea44f', linestyle='--', linewidth=0.8, alpha=0.7)
        ax_rsi.fill_between(df_zoom['date'].values, 30, 70, color='#8b949e', alpha=0.05)
        ax_rsi.set_ylim(10, 90)
        ax_rsi.set_ylabel('RSI', color='#8b949e')
        ax_rsi.tick_params(axis='both', colors='#8b949e')
        ax_rsi.grid(True, color='#21262d', linestyle=':', alpha=0.6)
        ax_rsi.legend(loc='upper left', frameon=True, facecolor='#161b22', edgecolor='#30363d', fontsize=8)
        
        ax_rsi.xaxis.set_major_formatter(mdates.DateFormatter('%b %d, %Y'))
        fig2.autofmt_xdate()
        plt.tight_layout()
        
        trade_path = os.path.join(ARTIFACTS_DIR, f"{ticker}_trade_sr_chart.png")
        plt.savefig(trade_path, dpi=150, facecolor='#0d1117')
        plt.close()
        return True
    except Exception as e:
        print(f"Error drawing charts for {ticker}: {e}")
        return False


def generate_sr_chart(ticker):
    import matplotlib.dates as mdates
    try:
        df_full = load_history_df(ticker, 260)
        if df_full is None:
            return False
            
        last_close = float(df_full['close'].iloc[-1])
        timeframes = [
            {"days": 252, "label": "1-Year", "window": 5},
            {"days": 60, "label": "3-Month", "window": 3},
            {"days": 20, "label": "1-Month", "window": 2}
        ]
        selected_tf = timeframes[0]
        supports, resistances = [], []
        for tf in timeframes:
            df_slice = df_full.tail(tf["days"]).copy()
            sups, refs = detect_levels(df_slice, window=tf["window"], threshold_pct=0.015)
            active_sups = [s for s in sups if s <= last_close]
            closest_sup = max(active_sups) if active_sups else None
            if closest_sup is not None and (last_close - closest_sup) / closest_sup <= 0.12:
                selected_tf = tf
                supports, resistances = sups, refs
                break
        else:
            selected_tf = timeframes[1]
            df_slice = df_full.tail(selected_tf["days"]).copy()
            supports, resistances = detect_levels(df_slice, window=selected_tf["window"], threshold_pct=0.015)
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(14, 8))
        fig.suptitle(f'{ticker} - Support & Resistance ({selected_tf["label"]} Detection Overlay)', fontsize=16, color='#58a6ff', weight='bold')
        ax.vlines(df_full['date'], df_full['low'], df_full['high'], color='#8b949e', linewidth=1, zorder=2)
        up_days = df_full[df_full['close'] >= df_full['open']]
        down_days = df_full[df_full['close'] < df_full['open']]
        ax.bar(up_days['date'], up_days['close'] - up_days['open'], bottom=up_days['open'], color='#2ea44f', width=0.6, edgecolor='#2ea44f', zorder=3)
        ax.bar(down_days['date'], down_days['open'] - down_days['close'], bottom=down_days['close'], color='#d73a49', width=0.6, edgecolor='#d73a49', zorder=3)
        dates = df_full['date'].values
        start_date = dates[0]
        end_date = dates[-1]
        y_min, y_max = df_full['low'].min(), df_full['high'].max()
        for idx, sup in enumerate(supports):
            if y_min <= sup <= y_max:
                ax.hlines(sup, start_date, end_date, colors='#2ea44f', linestyles=':', linewidth=1.5, zorder=4)
                ax.text(start_date, sup, f' S{idx+1}: ${sup:.2f}', color='#2ea44f', va='bottom', ha='left', weight='bold', fontsize=9)
        for idx, res in enumerate(resistances):
            if y_min <= res <= y_max:
                ax.hlines(res, start_date, end_date, colors='#f85149', linestyles=':', linewidth=1.5, zorder=4)
                ax.text(start_date, res, f' R{idx+1}: ${res:.2f}', color='#f85149', va='bottom', ha='left', weight='bold', fontsize=9)
        ax.text(0.98, 0.02, f"S/R Level Source: {selected_tf['label']} Window", transform=ax.transAxes, color='#8b949e', ha='right', va='bottom', style='italic', fontsize=10)
        ax.set_ylabel('Price ($)', fontsize=12, color='#8b949e')
        ax.grid(True, color='#21262d', linestyle=':', alpha=0.5)
        ax.tick_params(axis='both', colors='#8b949e')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        fig.autofmt_xdate()
        plt.tight_layout()
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        save_path = os.path.join(ARTIFACTS_DIR, f"{ticker}_sr_chart.png")
        plt.savefig(save_path, dpi=150, facecolor='#0d1117')
        plt.close()
        return True
    except Exception:
        return False


# Columns descriptions for user reference
COLUMNS_DESC = {
    "ticker": "Unique stock ticker symbol (e.g., AAPL)",
    "exchange": "Exchange where listed (NASDAQ, NYSE, NYSEMKT)",
    "type": "Asset type (stock, etf)",
    "date": "Date of the latest calculation (YYYYMMDD)",
    "price": "Latest closing price",
    "change_percent": "Daily price change percent",
    "volume": "Latest trading volume",
    "avg_volume_20": "20-day average daily volume",
    "avg_volume_50": "50-day average daily volume",
    "sma_20": "20-day Simple Moving Average",
    "sma_50": "50-day Simple Moving Average",
    "sma_200": "200-day Simple Moving Average",
    "ema_9": "9-day Exponential Moving Average",
    "ema_20": "20-day Exponential Moving Average",
    "ema_12": "12-day Exponential Moving Average",
    "ema_26": "26-day Exponential Moving Average",
    "ema_50": "50-day Exponential Moving Average",
    "rsi_14": "14-day Relative Strength Index (Wilder's method)",
    "macd": "MACD line (12-day EMA - 26-day EMA)",
    "macd_signal": "MACD signal line (9-day EMA of MACD)",
    "macd_hist": "MACD histogram (MACD - signal)",
    "high_52w": "52-week highest price",
    "low_52w": "52-week lowest price",
    "dist_from_high_52w_pct": "Percentage distance from 52-week high (usually negative or 0)",
    "dist_from_low_52w_pct": "Percentage distance from 52-week low (usually positive or 0)",
    "return_1d": "1-day return % (same as change_percent)",
    "return_5d": "5-day return %",
    "return_20d": "20-day return %",
    "return_250d": "250-day return % (~1 year)",
    "volatility_20": "20-day historical volatility (standard deviation of daily returns in percent)",
    "filepath": "Absolute path to the raw daily price history text file"
}

def get_connection():
    if not os.path.exists(DB_PATH):
        print("Database not found! Triggering initial index...")
        run_indexing()
    return sqlite3.connect(DB_PATH)

@mcp.tool()
def get_available_fields() -> str:
    """Returns a table of all columns in the stocks database with descriptions."""
    data = [{"Field": k, "Description": v} for k, v in COLUMNS_DESC.items()]
    df = pd.DataFrame(data)
    return df.to_markdown(index=False)

@mcp.tool()
def query_screener_db(sql_query: str) -> str:
    """Run a custom read-only SQL query on the 'stocks' table.
    
    This tool allows running complex SQL queries to screen stocks. Only SELECT queries are allowed.
    Example: SELECT ticker, price, rsi_14, sma_200 FROM stocks WHERE price > 10 AND rsi_14 < 30 AND price > sma_200 ORDER BY rsi_14 ASC LIMIT 20
    
    Args:
        sql_query: The SQL SELECT statement to execute.
    """
    cleaned = sql_query.strip()
    if not cleaned.upper().startswith("SELECT"):
        return "Error: Only read-only SELECT queries are allowed."
        
    try:
        conn = get_connection()
        df = pd.read_sql_query(sql_query, conn)
        conn.close()
        
        if df.empty:
            return "No results match the query."
            
        return df.to_markdown(index=False)
    except Exception as e:
        return f"Error executing query: {str(e)}"

@mcp.tool()
def screen_stocks(
    exchange: str = None,
    asset_type: str = None,
    min_price: float = None,
    max_price: float = None,
    min_volume: float = None,
    min_rsi: float = None,
    max_rsi: float = None,
    rsi_state: str = None,
    price_vs_sma: str = None,
    sort_by: str = "volume",
    sort_desc: bool = True,
    limit: int = 50
) -> str:
    """Filter stocks using a pre-defined structured screener.
    
    Args:
        exchange: Filter by exchange (NASDAQ, NYSE, NYSEMKT).
        asset_type: Filter by asset type (stock, etf).
        min_price: Minimum closing price.
        max_price: Maximum closing price.
        min_volume: Minimum trading volume.
        min_rsi: Minimum 14-day RSI.
        max_rsi: Maximum 14-day RSI.
        rsi_state: Overbought (RSI > 70) or Oversold (RSI < 30).
        price_vs_sma: Check relation to SMA 200, options: 'above_sma200', 'below_sma200', 'above_sma50', 'below_sma50'.
        sort_by: Column to sort results by (e.g. price, volume, change_percent, rsi_14, volatility_20).
        sort_desc: Sort descending if True, ascending if False.
        limit: Max number of stocks to return (default 50).
    """
    query = "SELECT ticker, exchange, type, price, change_percent, volume, rsi_14, sma_50, sma_200 FROM stocks WHERE 1=1"
    params = []
    
    if exchange:
        query += " AND exchange = ?"
        params.append(exchange.upper())
    if asset_type:
        query += " AND type = ?"
        params.append(asset_type.lower())
    if min_price is not None:
        query += " AND price >= ?"
        params.append(min_price)
    if max_price is not None:
        query += " AND price <= ?"
        params.append(max_price)
    if min_volume is not None:
        query += " AND volume >= ?"
        params.append(min_volume)
    if min_rsi is not None:
        query += " AND rsi_14 >= ?"
        params.append(min_rsi)
    if max_rsi is not None:
        query += " AND rsi_14 <= ?"
        params.append(max_rsi)
        
    if rsi_state:
        state = rsi_state.lower()
        if state == "oversold":
            query += " AND rsi_14 <= 30"
        elif state == "overbought":
            query += " AND rsi_14 >= 70"
            
    if price_vs_sma:
        condition = price_vs_sma.lower()
        if condition == "above_sma200":
            query += " AND price > sma_200 AND sma_200 IS NOT NULL"
        elif condition == "below_sma200":
            query += " AND price < sma_200 AND sma_200 IS NOT NULL"
        elif condition == "above_sma50":
            query += " AND price > sma_50 AND sma_50 IS NOT NULL"
        elif condition == "below_sma50":
            query += " AND price < sma_50 AND sma_50 IS NOT NULL"
            
    if sort_by not in COLUMNS_DESC:
        sort_by = "volume"
        
    direction = "DESC" if sort_desc else "ASC"
    query += f" ORDER BY {sort_by} {direction} LIMIT ?"
    params.append(limit)
    
    try:
        conn = get_connection()
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return "No stocks match the criteria."
            
        return df.to_markdown(index=False)
    except Exception as e:
        return f"Error executing structured screen: {str(e)}"

@mcp.tool()
def get_stock_history(ticker: str, limit: int = 100) -> str:
    """Retrieve raw historical daily price and volume data for a specific stock ticker.
    
    Args:
        ticker: The stock symbol (e.g. 'AAPL' or 'MSFT').
        limit: Number of recent trading days to fetch (max 500).
    """
    ticker = ticker.upper()
    try:
        df = load_history_df(ticker, limit)
        if df is None:
            return f"Error: No historical data found for {ticker}."
        df['date'] = df['date'].dt.strftime('%Y%m%d')
        df['openint'] = 0
        return df.to_markdown(index=False)
    except Exception as e:
        return f"Error reading stock history: {str(e)}"


@mcp.tool()
def get_stock_history_5min(ticker: str, limit: int = 100) -> str:
    """Retrieve raw historical 5-minute price and volume data for a specific stock ticker.
    
    Args:
        ticker: The stock symbol (e.g. 'AAPL' or 'MSFT').
        limit: Number of recent 5-minute bars to fetch (max 1000).
    """
    ticker = ticker.upper()
    try:
        limit = min(limit, 1000)
        df = load_history_df_5min(ticker, limit)
        if df is None:
            return f"Error: No historical 5-minute data found for {ticker}."
        
        df['datetime'] = df['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
        df = df[['datetime', 'open', 'high', 'low', 'close', 'vol']]
        return df.to_markdown(index=False)
    except Exception as e:
        return f"Error reading stock history: {str(e)}"


@mcp.tool()
def get_stock_chart(
    ticker: str, 
    days: int = 60,
    support_prices: list[float] = None,
    resistance_prices: list[float] = None,
    stop_losses: list[float] = None,
    target_prices: list[float] = None
) -> str:
    """Generate a high-quality dark-themed candlestick chart with 50/200 SMAs and support/resistance lines.
    
    The chart is saved as a PNG image, and a file path along with a markdown link is returned.
    
    Args:
        ticker: The stock symbol (e.g. 'AAPL', 'TSLA').
        days: Number of trading days to show in the chart (default 60, max 200).
        support_prices: Optional list of price levels to draw as yellow dotted support lines.
        resistance_prices: Optional list of price levels to draw as red dotted resistance lines.
        stop_losses: Optional list of price levels to draw as red dashed stop-loss lines.
        target_prices: Optional list of price levels to draw as green dashed profit-target lines.
    """
    ticker = ticker.upper()
    try:
        # Create charts directory inside workspace
        charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
        os.makedirs(charts_dir, exist_ok=True)
        chart_path = os.path.join(charts_dir, f"{ticker}_chart.png")
        
        # Read history data from database
        df = load_history_df(ticker, days + 220)
        if df is None:
            return f"Error: No historical data found for {ticker}."
            
        # Ensure 'volume' column is present
        if 'vol' in df.columns and 'volume' not in df.columns:
            df['volume'] = df['vol']
        
        # Compute indicators
        df['sma_200'] = df['close'].rolling(200).mean()
        df['sma_50'] = df['close'].rolling(50).mean()
        
        # Slice to days window
        df_plot = df.tail(min(days, 200)).copy()
        if len(df_plot) < 2:
            return f"Error: Ticker '{ticker}' does not have enough historical data."
            
        # Create chart
        plt.style.use('dark_background')
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
        
        fig.suptitle(f'{ticker} - Daily Candlestick & Moving Averages', fontsize=16, color='#58a6ff', weight='bold')
        
        # Plot Candlesticks
        ax1.vlines(df_plot['date'], df_plot['low'], df_plot['high'], color='#8b949e', linewidth=1, zorder=2)
        
        up_days = df_plot[df_plot['close'] >= df_plot['open']]
        down_days = df_plot[df_plot['close'] < df_plot['open']]
        
        ax1.bar(up_days['date'], up_days['close'] - up_days['open'], bottom=up_days['open'], 
                color='#2ea44f', width=0.6, edgecolor='#2ea44f', zorder=3, label='Bullish Day')
        ax1.bar(down_days['date'], down_days['open'] - down_days['close'], bottom=down_days['close'], 
                color='#d73a49', width=0.6, edgecolor='#d73a49', zorder=3, label='Bearish Day')
                
        # Plot SMAs
        if df_plot['sma_200'].notna().any():
            ax1.plot(df_plot['date'], df_plot['sma_200'], label='200-day SMA', color='#ff7f0e', linestyle='--', linewidth=1.5, zorder=4)
        if df_plot['sma_50'].notna().any():
            ax1.plot(df_plot['date'], df_plot['sma_50'], label='50-day SMA', color='#1f77b4', linestyle=':', linewidth=1.5, zorder=4)
            
        # Draw optional trade support/resistance/stop/target lines
        if support_prices is not None:
            for idx, p in enumerate(support_prices):
                label = f"Support: ${p:.2f}" if idx == 0 else ""
                ax1.axhline(y=p, color='#ffea7f', linestyle=':', linewidth=1.5, zorder=1, label=label)
        if resistance_prices is not None:
            for idx, p in enumerate(resistance_prices):
                label = f"Resistance: ${p:.2f}" if idx == 0 else ""
                ax1.axhline(y=p, color='#ff7f7f', linestyle=':', linewidth=1.5, zorder=1, label=label)
        if stop_losses is not None:
            for idx, p in enumerate(stop_losses):
                label = f"Stop Loss: ${p:.2f}" if idx == 0 else ""
                ax1.axhline(y=p, color='#ea4a5a', linestyle='--', linewidth=1.5, zorder=1, label=label)
        if target_prices is not None:
            for idx, p in enumerate(target_prices):
                label = f"Target: ${p:.2f}" if idx == 0 else ""
                ax1.axhline(y=p, color='#34d058', linestyle='--', linewidth=1.5, zorder=1, label=label)
            
        ax1.set_ylabel('Price ($)', fontsize=12, color='#8b949e')
        
        # Clean legend
        handles, labels = ax1.get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        ax1.legend(by_label.values(), by_label.keys(), loc='upper left', frameon=True, facecolor='#161b22', edgecolor='#30363d')
        ax1.grid(True, color='#21262d', linestyle=':', alpha=0.6)
        ax1.tick_params(axis='y', colors='#8b949e')
        
        # Volume
        colors = ['#2ea44f' if close >= open else '#d73a49' for close, open in zip(df_plot['close'], df_plot['open'])]
        ax2.bar(df_plot['date'], df_plot['vol'], color=colors, alpha=0.8, width=0.6)
        ax2.set_ylabel('Volume', fontsize=12, color='#8b949e')
        ax2.grid(True, color='#21262d', linestyle=':', alpha=0.6)
        ax2.tick_params(axis='both', colors='#8b949e')
        ax2.ticklabel_format(style='plain', axis='y')
        
        # Dates
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %d, %Y'))
        ax2.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=max(1, days // 15)))
        fig.autofmt_xdate()
        
        plt.tight_layout()
        plt.savefig(chart_path, dpi=150, facecolor='#0d1117')
        plt.close()
        
        url_path = chart_path.replace('\\', '/')
        return f"Successfully generated chart for {ticker}.\nSaved to: {chart_path}\nLink: [View Chart for {ticker}](file:///{url_path})"
    except Exception as e:
        return f"Error generating chart: {str(e)}"


@mcp.tool()
def find_support_resistance(ticker: str, days: int = 150) -> str:
    """Analyze historical price action to identify key support and resistance levels.
    
    The tool finds swing highs and lows and clusters them to find strong congestion zones.
    
    Args:
        ticker: The stock symbol (e.g. 'AAPL', 'TSLA').
        days: Lookback window in trading days to analyze (default 150, max 300).
    """
    ticker = ticker.upper()
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT filepath, price FROM stocks WHERE ticker = ?", (ticker,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return f"Error: Ticker '{ticker}' not found in the database."
            
        filepath, current_price = row
        if not os.path.exists(filepath):
            return f"Error: History file for ticker '{ticker}' not found at {filepath}."
            
        from collections import deque
        with open(filepath, 'r') as f:
            header_line = f.readline()
            header = header_line.strip().replace('<', '').replace('>', '').lower().split(',')
            lines = deque(f, maxlen=min(days, 300))
            
        data = []
        for line in lines:
            row_vals = line.strip().split(',')
            if len(row_vals) == len(header):
                data.append(row_vals)
                
        df = pd.DataFrame(data, columns=header)
        for col in ['open', 'high', 'low', 'close', 'vol']:
            df[col] = pd.to_numeric(df[col])
            
        if len(df) < 20:
            return f"Error: Ticker '{ticker}' has insufficient price history."
            
        # 1. Calculate local minimums and maximums (Swing Highs and Lows)
        window = 10
        df['is_min'] = df['low'] == df['low'].rolling(window*2+1, center=True).min()
        df['is_max'] = df['high'] == df['high'].rolling(window*2+1, center=True).max()
        
        raw_mins = df[df['is_min']]['low'].tolist()
        raw_maxs = df[df['is_max']]['high'].tolist()
        
        # 2. Cluster levels (tolerance = 1.5% of average price)
        def cluster_levels(levels, tolerance=0.015):
            if not levels:
                return []
            levels = sorted(levels)
            clusters = []
            curr_c = [levels[0]]
            for val in levels[1:]:
                avg = sum(curr_c) / len(curr_c)
                if (val - avg) / avg <= tolerance:
                    curr_c.append(val)
                else:
                    clusters.append(curr_c)
                    curr_c = [val]
            clusters.append(curr_c)
            
            results = []
            for c in clusters:
                avg_p = sum(c) / len(c)
                results.append({
                    'price': round(avg_p, 2),
                    'strength': len(c)
                })
            return results
            
        supports = cluster_levels(raw_mins)
        resistances = cluster_levels(raw_maxs)
        
        # 3. Categorize by current price
        all_levels = supports + resistances
        unique_levels = {}
        for l in all_levels:
            p = l['price']
            matched = False
            for exist_p in list(unique_levels.keys()):
                if abs(p - exist_p) / exist_p <= 0.015:
                    if l['strength'] > unique_levels[exist_p]['strength']:
                        unique_levels[p] = l
                        del unique_levels[exist_p]
                    matched = True
                    break
            if not matched:
                unique_levels[p] = l
                
        final_levels = list(unique_levels.values())
        
        # Separate into support and resistance relative to current price
        s_list = []
        r_list = []
        for l in final_levels:
            dist_pct = ((l['price'] / current_price) - 1) * 100
            l['dist_pct'] = f"{dist_pct:+.2f}%"
            l['raw_dist'] = abs(dist_pct)
            
            if l['price'] < current_price:
                s_list.append(l)
            else:
                r_list.append(l)
                
        s_list = sorted(s_list, key=lambda x: x['price'], reverse=True)
        r_list = sorted(r_list, key=lambda x: x['price'])
        
        output = [f"## Support & Resistance Analysis: {ticker} (Current Price: ${current_price:.2f})"]
        
        output.append("\n### Support Levels (Buying Floors)")
        if s_list:
            output.append(pd.DataFrame(s_list)[['price', 'strength', 'dist_pct']].rename(
                columns={'price': 'Price ($)', 'strength': 'Test Count (Strength)', 'dist_pct': 'Distance (%)'}
            ).to_markdown(index=False))
        else:
            output.append("*No major support levels found in lookback window.*")
            
        output.append("\n### Resistance Levels (Selling Ceilings)")
        if r_list:
            output.append(pd.DataFrame(r_list)[['price', 'strength', 'dist_pct']].rename(
                columns={'price': 'Price ($)', 'strength': 'Test Count (Strength)', 'dist_pct': 'Distance (%)'}
            ).to_markdown(index=False))
        else:
            output.append("*No major resistance levels found in lookback window.*")
            
        return "\n".join(output)
    except Exception as e:
        return f"Error analyzing support/resistance: {str(e)}"

@mcp.tool()
def reindex_database() -> str:
    """Force a scan and recalculation of all files in the stock data directory.
    
    Useful if new stock files are added or existing data files are updated.
    """
    try:
        import subprocess
        import sys
        print("Reindexing database...")
        result = subprocess.run([sys.executable, "indexer.py"], capture_output=True, text=True, check=True)
        return f"Database reindexed successfully!\nLog Output:\n{result.stdout}"
    except Exception as e:
        return f"Error reindexing database: {str(e)}"


@mcp.tool()
def update_database_from_file(file_path: str) -> str:
    r"""Update the local daily historical data files using a downloaded Stooq daily update text file (e.g. 'C:\Users\saiba\Downloads\20260701_d.txt') and rebuild the screener database.
    
    This appends records to the raw text files on disk, updates the historical_prices table, and recalculates indicators.
    
    Args:
        file_path: The absolute path to the downloaded Stooq update text file.
    """
    import os
    if not os.path.exists(file_path):
        return f"Error: The file '{file_path}' does not exist."
        
    try:
        import io
        from contextlib import redirect_stdout
        
        f = io.StringIO()
        with redirect_stdout(f):
            success = update_from_file(file_path)
            if success:
                run_indexing()
                
        output = f.getvalue()
        if success:
            return f"Database updated successfully from '{file_path}'!\n\nLog Details:\n{output}"
        else:
            return f"Failed to update database from '{file_path}'.\n\nLog Details:\n{output}"
            
    except Exception as e:
        return f"Error running update: {str(e)}"



@mcp.tool()
def get_support_resistance_levels(ticker: str) -> str:
    """Detect and return support and resistance levels for a stock ticker based on dynamic historical timeframes, generating an annotated chart.
    
    Args:
        ticker: The stock symbol (e.g. 'AAPL', 'MSFT').
    """
    ticker = ticker.upper()
    try:

        import os
        import pandas as pd
        from collections import deque
        
        
            
        success = generate_sr_chart(ticker)
        if not success:
            return f"Error: Failed to generate support/resistance chart for {ticker}."
            
        df_full = load_history_df(ticker, 260)
        if df_full is None:
            return f"Error: No historical data found for {ticker}."
        
        last_close = float(df_full['close'].iloc[-1])
        
        timeframes = [
            {"days": 252, "label": "1-Year", "window": 5},
            {"days": 60, "label": "3-Month", "window": 3},
            {"days": 20, "label": "1-Month", "window": 2}
        ]
        
        selected_tf = timeframes[0]
        supports, resistances = [], []
        
        for tf in timeframes:
            df_slice = df_full.tail(tf["days"]).copy()
            sups, refs = detect_levels(df_slice, window=tf["window"], threshold_pct=0.015)
            active_sups = [s for s in sups if s <= last_close]
            closest_sup = max(active_sups) if active_sups else None
            
            if closest_sup is not None and (last_close - closest_sup) / closest_sup <= 0.12:
                selected_tf = tf
                supports, resistances = sups, refs
                break
        else:
            selected_tf = timeframes[1]
            df_slice = df_full.tail(selected_tf["days"]).copy()
            supports, resistances = detect_levels(df_slice, window=selected_tf["window"], threshold_pct=0.015)
            
        chart_path = os.path.join(ARTIFACTS_DIR, f"{ticker}_sr_chart.png")
        url_path = chart_path.replace('\\', '/')
        
        sups_str = ", ".join([f"${x:.2f}" for x in supports]) if supports else "None"
        res_str = ", ".join([f"${x:.2f}" for x in resistances]) if resistances else "None"
        
        return (
            f"### Support & Resistance Levels for {ticker} ({selected_tf['label']} Window)\n"
            f"* **Current Close**: ${last_close:.2f}\n"
            f"* **Supports**: {sups_str}\n"
            f"* **Resistances**: {res_str}\n"
            f"* **Chart Saved To**: {chart_path}\n"
            f"* **Link**: [View Support/Resistance Chart for {ticker}](file:///{url_path})"
        )
    except Exception as e:
        return f"Error executing support/resistance detection: {str(e)}"


def check_structural_uptrend(df, window=10):
    """Checks if a stock has a structural higher-high and higher-low pattern over the last 1 year (252 trading days)."""
    import numpy as np
    if len(df) < 100:
        return False
    
    df_1y = df.tail(252).reset_index(drop=True)
    highs = df_1y['high'].values
    lows = df_1y['low'].values
    
    peaks = []
    troughs = []
    
    for i in range(window, len(df_1y) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            peaks.append(highs[i])
        if lows[i] == min(lows[i-window:i+window+1]):
            troughs.append(lows[i])
            
    if len(peaks) < 2:
        peaks.append(float(np.percentile(highs, 25)))
        peaks.append(float(np.percentile(highs, 75)))
    if len(troughs) < 2:
        troughs.append(float(np.percentile(lows, 25)))
        troughs.append(float(np.percentile(lows, 75)))
        
    half_p = len(peaks) // 2
    first_peaks = np.mean(peaks[:half_p])
    second_peaks = np.mean(peaks[half_p:])
    
    half_t = len(troughs) // 2
    first_troughs = np.mean(troughs[:half_t])
    second_troughs = np.mean(troughs[half_t:])
    
    return (second_peaks > first_peaks * 0.98) and (second_troughs > first_troughs * 0.98)


def check_structural_downtrend(df, window=10):
    """Checks if a stock has a structural lower-high and lower-low pattern over the last 1 year (252 trading days)."""
    import numpy as np
    if len(df) < 100:
        return False
    
    df_1y = df.tail(252).reset_index(drop=True)
    highs = df_1y['high'].values
    lows = df_1y['low'].values
    
    peaks = []
    troughs = []
    
    for i in range(window, len(df_1y) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            peaks.append(highs[i])
        if lows[i] == min(lows[i-window:i+window+1]):
            troughs.append(lows[i])
            
    if len(peaks) < 2:
        peaks.append(float(np.percentile(highs, 75)))
        peaks.append(float(np.percentile(highs, 25)))
    if len(troughs) < 2:
        troughs.append(float(np.percentile(lows, 75)))
        troughs.append(float(np.percentile(lows, 25)))
        
    half_p = len(peaks) // 2
    first_peaks = np.mean(peaks[:half_p])
    second_peaks = np.mean(peaks[half_p:])
    
    half_t = len(troughs) // 2
    first_troughs = np.mean(troughs[:half_t])
    second_troughs = np.mean(troughs[half_t:])
    
    return (second_peaks < first_peaks * 1.02) and (second_troughs < first_troughs * 1.02)


def prepare_indicators(df):
    import numpy as np
    import pandas as pd
    
    close = df['close']
    high = df['high']
    low = df['low']
    vol = df['vol']
    
    df['ema_9'] = close.ewm(span=9, adjust=False).mean()
    df['ema_20'] = close.ewm(span=20, adjust=False).mean()
    df['sma_50'] = close.rolling(50).mean()
    df['sma_200'] = close.rolling(200).mean()
    df['avg_vol_20'] = vol.rolling(20).mean()
    
    # Calculate ATR-14
    df['prev_close'] = close.shift(1)
    df['tr'] = np.maximum(
        high - low,
        np.maximum(
            (high - df['prev_close']).abs(),
            (low - df['prev_close']).abs()
        )
    )
    df['atr_14'] = df['tr'].rolling(14).mean()
    
    # Calculate Bollinger Bands
    df['bollinger_mid'] = close.rolling(20).mean()
    df['bollinger_std'] = close.rolling(20).std()
    df['bollinger_upper'] = df['bollinger_mid'] + 2 * df['bollinger_std']
    df['bollinger_lower'] = df['bollinger_mid'] - 2 * df['bollinger_std']
    
    # Calculate RSI-14
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    df['rsi_14'] = 100.0 - (100.0 / (1.0 + rs))
    
    return df


def get_closest_sr(df, price):

    timeframes = [
        {"days": 252, "label": "1-Year", "window": 5},
        {"days": 60, "label": "3-Month", "window": 3},
        {"days": 20, "label": "1-Month", "window": 2}
    ]
    selected_tf = timeframes[0]
    supports, resistances = [], []
    
    for tf in timeframes:
        df_slice = df.tail(tf["days"]).copy()
        sups, refs = detect_levels(df_slice, window=tf["window"], threshold_pct=0.015)
        active_sups = [s for s in sups if s <= price]
        closest_sup = max(active_sups) if active_sups else None
        
        if closest_sup is not None and (price - closest_sup) / closest_sup <= 0.12:
            selected_tf = tf
            supports, resistances = sups, refs
            break
    else:
        selected_tf = timeframes[1]
        df_slice = df.tail(selected_tf["days"]).copy()
        supports, resistances = detect_levels(df_slice, window=selected_tf["window"], threshold_pct=0.015)
        
    return supports, resistances, selected_tf


def evaluate_dynamic_strategy(df, strategy_def, ticker):
    import pandas as pd
    import numpy as np

    if len(df) < 50:
        return None
        
    row_last = df.iloc[-1]
    row_prev = df.iloc[-2]
    
    price = float(row_last['close'])
    
    rules = load_rules()
    min_price = rules.get("min_price", 15.0)
    min_volume = rules.get("min_volume", 1000000)
    
    avg_vol = float(row_last['avg_vol_20']) if not pd.isna(row_last['avg_vol_20']) else 0.0
    if price < min_price or avg_vol < min_volume:
        return None
        
    supports, resistances, selected_tf = get_closest_sr(df, price)
    supports_below_entry = [s for s in supports if s < price]
    support = max(supports_below_entry) if supports_below_entry else float(row_last['low'])
    
    resistances_above_entry = [r for r in resistances if r > price]
    resistance = min(resistances_above_entry) if resistances_above_entry else float(row_last['high'])
    
    has_structural_trend = check_structural_uptrend(df)
    has_structural_downtrend = check_structural_downtrend(df)
    
    fakeout_low = min(float(row_last['low']), float(row_prev['low']))
    fakeout_high = max(float(row_last['high']), float(row_prev['high']))
    
    context = {
        'open': float(row_last['open']),
        'high': float(row_last['high']),
        'low': float(row_last['low']),
        'close': float(row_last['close']),
        'vol': float(row_last['vol']),
        
        'ema_9': float(row_last['ema_9']) if not pd.isna(row_last['ema_9']) else 0.0,
        'ema_20': float(row_last['ema_20']) if not pd.isna(row_last['ema_20']) else 0.0,
        'sma_50': float(row_last['sma_50']) if not pd.isna(row_last['sma_50']) else 0.0,
        'sma_200': float(row_last['sma_200']) if not pd.isna(row_last['sma_200']) else 0.0,
        'atr': float(row_last['atr_14']) if not pd.isna(row_last['atr_14']) else 0.0,
        
        'bollinger_mid': float(row_last['bollinger_mid']) if not pd.isna(row_last['bollinger_mid']) else 0.0,
        'bollinger_upper': float(row_last['bollinger_upper']) if not pd.isna(row_last['bollinger_upper']) else 0.0,
        'bollinger_lower': float(row_last['bollinger_lower']) if not pd.isna(row_last['bollinger_lower']) else 0.0,
        
        'prev_open': float(row_prev['open']),
        'prev_high': float(row_prev['high']),
        'prev_low': float(row_prev['low']),
        'prev_close': float(row_prev['close']),
        
        'prev_ema_9': float(row_prev['ema_9']) if not pd.isna(row_prev['ema_9']) else 0.0,
        'prev_ema_20': float(row_prev['ema_20']) if not pd.isna(row_prev['ema_20']) else 0.0,
        'prev_sma_50': float(row_prev['sma_50']) if not pd.isna(row_prev['sma_50']) else 0.0,
        'prev_sma_200': float(row_prev['sma_200']) if not pd.isna(row_prev['sma_200']) else 0.0,
        
        'prev_bollinger_mid': float(row_prev['bollinger_mid']) if not pd.isna(row_prev['bollinger_mid']) else 0.0,
        'prev_bollinger_upper': float(row_prev['bollinger_upper']) if not pd.isna(row_prev['bollinger_upper']) else 0.0,
        'prev_bollinger_lower': float(row_prev['bollinger_lower']) if not pd.isna(row_prev['bollinger_lower']) else 0.0,
        
        'support': float(support),
        'resistance': float(resistance),
        'fakeout_low': float(fakeout_low),
        'fakeout_high': float(fakeout_high),
        
        'structural_uptrend': bool(has_structural_trend),
        'structural_downtrend': bool(has_structural_downtrend),
        
        'min_atr_multiple_sl': float(rules.get("min_atr_multiple_sl", 2.0)),
        
        'market_cap': 0.0,
        'avg_vol_20': float(avg_vol),
        'prev_avg_vol_20': float(row_prev['avg_vol_20']) if not pd.isna(row_prev['avg_vol_20']) else 0.0,
        
        'rsi': float(row_last['rsi_14']) if not pd.isna(row_last['rsi_14']) else 50.0,
        'prev_rsi': float(row_prev['rsi_14']) if not pd.isna(row_prev['rsi_14']) else 50.0,
        
        'min': min,
        'max': max,
        'abs': abs
    }
    
    tech_conditions = [c for c in strategy_def["conditions"] if "market_cap" not in c]
    mcap_conditions = [c for c in strategy_def["conditions"] if "market_cap" in c]
    
    try:
        for cond in tech_conditions:
            result = eval(cond, {"__builtins__": None}, context)
            if not result:
                return None
    except Exception:
        return None
        
    if mcap_conditions:
        overview = get_company_overview(ticker)
        try:
            mc_str = overview.get("MarketCapitalization", "0")
            market_cap = float(mc_str) if mc_str else 0.0
        except Exception:
            market_cap = 0.0
        context['market_cap'] = float(market_cap)
        
        try:
            for cond in mcap_conditions:
                result = eval(cond, {"__builtins__": None}, context)
                if not result:
                    return None
        except Exception:
            return None
        
    try:
        sl = eval(strategy_def["sl_formula"], {"__builtins__": None}, context)
        target = eval(strategy_def["target_formula"], {"__builtins__": None}, context)
    except Exception:
        return None
        
    eval_res = evaluate_trade(price, sl, target)
    if not eval_res:
        return None
        
    return {
        'price': price,
        'entry': price,
        'sl': float(sl),
        'target': float(target),
        'rr_ratio': eval_res['rr_ratio'],
        'shares': eval_res['shares'],
        'total_alloc': eval_res['total_alloc'],
        'total_risk': eval_res['total_risk'],
        'timeframe': selected_tf['label']
    }


def process_dsl_candidate(ticker, active_strategies):
    try:
        df = load_history_df(ticker, 260)
        if df is None or len(df) < 200:
            return []
            
        df = prepare_indicators(df)
        
        qualifying_setups = []
        for strat in active_strategies:
            res = evaluate_dynamic_strategy(df, strat, ticker)
            if res:
                res['ticker'] = ticker
                res['strategy'] = strat['name']
                res['target_timeframe'] = strat['target_timeframe']
                qualifying_setups.append(res)
                
        return qualifying_setups
    except Exception:
        return []


def process_candidate(ticker):
    try:
        import numpy as np
        df = load_history_df(ticker, 260)
        if df is None or len(df) < 200:
            return []
            
        close = df['close']
        high = df['high']
        low = df['low']
        vol = df['vol']
        open_val = df['open']
        
        df['ema_9'] = close.ewm(span=9, adjust=False).mean()
        df['ema_20'] = close.ewm(span=20, adjust=False).mean()
        df['sma_50'] = close.rolling(50).mean()
        df['sma_200'] = close.rolling(200).mean()
        df['avg_vol_20'] = vol.rolling(20).mean()
        
        # Calculate ATR-14
        df['prev_close'] = close.shift(1)
        df['tr'] = np.maximum(
            high - low,
            np.maximum(
                (high - df['prev_close']).abs(),
                (low - df['prev_close']).abs()
            )
        )
        df['atr_14'] = df['tr'].rolling(14).mean()
        
        row_last = df.iloc[-1]
        row_prev = df.iloc[-2]
        row_prev2 = df.iloc[-3] if len(df) >= 3 else row_prev
        
        price = float(row_last['close'])
        avg_vol = float(row_last['avg_vol_20'])
        
        # Basic liquidity check
        if price < 15 or avg_vol < 1000000:
            return []
            
        sma_50 = row_last['sma_50']
        sma_200 = row_last['sma_200']
        
        # Determine S/R levels
        timeframes = [
            {"days": 252, "label": "1-Year", "window": 5},
            {"days": 60, "label": "3-Month", "window": 3},
            {"days": 20, "label": "1-Month", "window": 2}
        ]
        selected_tf = timeframes[0]
        supports, resistances = [], []
        
        for tf in timeframes:
            df_slice = df.tail(tf["days"]).copy()
            sups, refs = detect_levels(df_slice, window=tf["window"], threshold_pct=0.015)
            active_sups = [s for s in sups if s <= price]
            closest_sup = max(active_sups) if active_sups else None
            
            if closest_sup is not None and (price - closest_sup) / closest_sup <= 0.12:
                selected_tf = tf
                supports, resistances = sups, refs
                break
        else:
            selected_tf = timeframes[1]
            df_slice = df.tail(selected_tf["days"]).copy()
            supports, resistances = detect_levels(df_slice, window=selected_tf["window"], threshold_pct=0.015)
            
        # Get stop loss anchor
        supports_below_entry = [s for s in supports if s < price]
        closest_sup_level = max(supports_below_entry) if supports_below_entry else float(row_last['low'])
        
        # Calculate ATR-based minimum Stop Loss
        rules = load_rules()
        atr_multiple = rules.get("min_atr_multiple_sl", 1.5)
        atr_val = float(row_last['atr_14']) if not pd.isna(row_last['atr_14']) else 0.0
        min_sl_dist = atr_multiple * atr_val
        sl_atr_level = price - min_sl_dist
        
        # Get target anchor
        resistances_above_entry = [r for r in resistances if r > price]
        if not resistances_above_entry:
            return [] # Reject blue-sky breakouts
        closest_res_level = min(resistances_above_entry)
        
        qualifying_setups = []
        
        # Common structural uptrend check (H-H and H-L)
        has_structural_trend = check_structural_uptrend(df)
        
        # Strategy 1: 9 EMA Touch (Very Very Short Term)
        # Conditions: price > 50 SMA > 200 SMA, and touching 9 EMA
        if price > sma_50 and sma_50 > sma_200 and has_structural_trend:
            ema_9_last = row_last['ema_9']
            ema_9_prev = row_prev['ema_9']
            touch = (row_last['low'] <= ema_9_last) and (row_last['high'] >= ema_9_last)
            pulled_back = row_prev['close'] > ema_9_prev
            
            if touch and pulled_back:
                sl_support = closest_sup_level
                sl = min(sl_support, sl_atr_level)
                target = closest_res_level - 0.50
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '9EMA_Touch', 'target_timeframe': '5-10 days'
                    })
                    
        # Strategy 2: 20 EMA Touch (Short Term)
        # Conditions: price > 50 SMA > 200 SMA, and touching 20 EMA
        if price > sma_50 and sma_50 > sma_200 and has_structural_trend:
            ema_20_last = row_last['ema_20']
            ema_20_prev = row_prev['ema_20']
            touch = (row_last['low'] <= ema_20_last) and (row_last['high'] >= ema_20_last)
            pulled_back = row_prev['close'] > ema_20_prev
            
            if touch and pulled_back:
                sl_support = closest_sup_level
                sl = min(sl_support, sl_atr_level)
                target = closest_res_level - 0.50
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '20EMA_Touch', 'target_timeframe': '10-20 days'
                    })
                    
        # Strategy 3: 50 SMA Fakeout (Medium Term)
        # Conditions: price > 50 SMA > 200 SMA, 1/2 red candles below 50 SMA, today green above 50 SMA
        if price > sma_50 and sma_50 > sma_200 and has_structural_trend:
            today_green = row_last['close'] > row_last['open']
            today_above_50 = row_last['close'] > sma_50
            yesterday_below_50 = row_prev['close'] < row_prev['sma_50']
            
            if today_green and today_above_50 and yesterday_below_50:
                fakeout_low = min(float(row_last['low']), float(row_prev['low']))
                sl_support = min(closest_sup_level, fakeout_low)
                sl = min(sl_support, sl_atr_level)
                target = closest_res_level - 0.50
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '50SMA_Fakeout', 'target_timeframe': '15-30 days'
                    })
                    
        # Strategy 4: 200 SMA Fakeout (Long Term)
        # Conditions: price > 200 SMA, 50 SMA can be below 200 SMA, but structural uptrend passes, 1/2 candles below 200 SMA, today green above 200 SMA
        if price > sma_200 and has_structural_trend:
            today_green = row_last['close'] > row_last['open']
            today_above_200 = row_last['close'] > sma_200
            yesterday_below_200 = row_prev['close'] < row_prev['sma_200']
            
            if today_green and today_above_200 and yesterday_below_200:
                fakeout_low = min(float(row_last['low']), float(row_prev['low']))
                sl_support = min(closest_sup_level, fakeout_low)
                sl = min(sl_support, sl_atr_level)
                target = closest_res_level - 0.50
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '200SMA_Fakeout', 'target_timeframe': '20-45 days'
                    })
                    
        return qualifying_setups
        
    except Exception:
        return []


def process_short_candidate(filepath):
    try:
        import os
        import pandas as pd
        import numpy as np
        from collections import deque

        filename = os.path.basename(filepath)
        ticker = filename.split('.')[0].upper()
        
        with open(filepath, 'r') as f:
            header_line = f.readline()
            header = header_line.strip().replace('<', '').replace('>', '').lower().split(',')
            lines = deque(f, maxlen=260)
        
        data = []
        for line in lines:
            row = line.strip().split(',')
            if len(row) == len(header):
                data.append(row)
        if len(data) < 200:
            return []
            
        df = pd.DataFrame(data, columns=header)
        for col in ['open', 'high', 'low', 'close', 'vol']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
        df = df.sort_values('date').reset_index(drop=True)
        
        close = df['close']
        high = df['high']
        low = df['low']
        vol = df['vol']
        open_val = df['open']
        
        df['ema_9'] = close.ewm(span=9, adjust=False).mean()
        df['ema_20'] = close.ewm(span=20, adjust=False).mean()
        df['sma_50'] = close.rolling(50).mean()
        df['sma_200'] = close.rolling(200).mean()
        df['avg_vol_20'] = vol.rolling(20).mean()
        
        # Calculate ATR-14
        df['prev_close'] = close.shift(1)
        df['tr'] = np.maximum(
            high - low,
            np.maximum(
                (high - df['prev_close']).abs(),
                (low - df['prev_close']).abs()
            )
        )
        df['atr_14'] = df['tr'].rolling(14).mean()
        
        row_last = df.iloc[-1]
        row_prev = df.iloc[-2]
        
        price = float(row_last['close'])
        avg_vol = float(row_last['avg_vol_20'])
        
        # Basic liquidity check
        if price < 15 or avg_vol < 1000000:
            return []
            
        sma_50 = row_last['sma_50']
        sma_200 = row_last['sma_200']
        
        # Determine S/R levels
        timeframes = [
            {"days": 252, "label": "1-Year", "window": 5},
            {"days": 60, "label": "3-Month", "window": 3},
            {"days": 20, "label": "1-Month", "window": 2}
        ]
        selected_tf = timeframes[0]
        supports, resistances = [], []
        
        for tf in timeframes:
            df_slice = df.tail(tf["days"]).copy()
            sups, refs = detect_levels(df_slice, window=tf["window"], threshold_pct=0.015)
            active_refs = [r for r in refs if r >= price]
            closest_ref = min(active_refs) if active_refs else None
            
            if closest_ref is not None and (closest_ref - price) / price <= 0.12:
                selected_tf = tf
                supports, resistances = sups, refs
                break
        else:
            selected_tf = timeframes[1]
            df_slice = df.tail(selected_tf["days"]).copy()
            supports, resistances = detect_levels(df_slice, window=selected_tf["window"], threshold_pct=0.015)
            
        # Get stop loss anchor (closest resistance above entry)
        resistances_above_entry = [r for r in resistances if r > price]
        closest_res_level = min(resistances_above_entry) if resistances_above_entry else float(row_last['high'])
        
        # Get target anchor (closest support below entry)
        supports_below_entry = [s for s in supports if s < price]
        if not supports_below_entry:
            return [] # Reject if no downside support target exists
        closest_sup_level = max(supports_below_entry)
        
        # Calculate ATR-based minimum Stop Loss for Short Trades
        rules = load_rules()
        atr_multiple = rules.get("min_atr_multiple_sl", 2.0)
        atr_val = float(row_last['atr_14']) if not pd.isna(row_last['atr_14']) else 0.0
        min_sl_dist = atr_multiple * atr_val
        sl_atr_level = price + min_sl_dist
        
        qualifying_setups = []
        
        # Common structural downtrend check (Lower Highs and Lower Lows)
        has_structural_trend = check_structural_downtrend(df)
        
        # Strategy 1: 9 EMA Touch (Short)
        # Conditions: price < 50 SMA < 200 SMA, and touching 9 EMA from below
        if price < sma_50 and sma_50 < sma_200 and has_structural_trend:
            ema_9_last = row_last['ema_9']
            ema_9_prev = row_prev['ema_9']
            touch = (row_last['low'] <= ema_9_last) and (row_last['high'] >= ema_9_last)
            pulled_back = row_prev['close'] < ema_9_prev
            
            if touch and pulled_back:
                sl_resistance = closest_res_level
                sl = max(sl_resistance, sl_atr_level)
                target = closest_sup_level
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '9EMA_Touch_Short', 'target_timeframe': '5-10 days'
                    })
                    
        # Strategy 2: 20 EMA Touch (Short)
        # Conditions: price < 50 SMA < 200 SMA, and touching 20 EMA from below
        if price < sma_50 and sma_50 < sma_200 and has_structural_trend:
            ema_20_last = row_last['ema_20']
            ema_20_prev = row_prev['ema_20']
            touch = (row_last['low'] <= ema_20_last) and (row_last['high'] >= ema_20_last)
            pulled_back = row_prev['close'] < ema_20_prev
            
            if touch and pulled_back:
                sl_resistance = closest_res_level
                sl = max(sl_resistance, sl_atr_level)
                target = closest_sup_level
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '20EMA_Touch_Short', 'target_timeframe': '10-20 days'
                    })
                    
        # Strategy 3: 50 SMA Fakeout (Short)
        # Conditions: price < 50 SMA < 200 SMA, 1/2 green candles above 50 SMA, today red below 50 SMA
        if price < sma_50 and sma_50 < sma_200 and has_structural_trend:
            today_red = row_last['close'] < row_last['open']
            today_below_50 = row_last['close'] < sma_50
            yesterday_above_50 = row_prev['close'] > row_prev['sma_50']
            
            if today_red and today_below_50 and yesterday_above_50:
                fakeout_high = max(float(row_last['high']), float(row_prev['high']))
                sl_resistance = max(closest_res_level, fakeout_high)
                sl = max(sl_resistance, sl_atr_level)
                target = closest_sup_level
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '50SMA_Fakeout_Short', 'target_timeframe': '15-30 days'
                    })
                    
        # Strategy 4: 200 SMA Fakeout (Short)
        # Conditions: price < 200 SMA, 50 SMA can be above 200 SMA, but structural downtrend passes, 1/2 candles above 200 SMA, today red below 200 SMA
        if price < sma_200 and has_structural_trend:
            today_red = row_last['close'] < row_last['open']
            today_below_200 = row_last['close'] < sma_200
            yesterday_above_200 = row_prev['close'] > row_prev['sma_200']
            
            if today_red and today_below_200 and yesterday_above_200:
                fakeout_high = max(float(row_last['high']), float(row_prev['high']))
                sl_resistance = max(closest_res_level, fakeout_high)
                sl = max(sl_resistance, sl_atr_level)
                target = closest_sup_level
                eval_res = evaluate_trade(price, sl, target)
                if eval_res:
                    qualifying_setups.append({
                        'ticker': ticker, 'price': price, 'entry': price, 'sl': sl, 'target': target,
                        'rr_ratio': eval_res['rr_ratio'], 'shares': eval_res['shares'],
                        'total_alloc': eval_res['total_alloc'], 'total_risk': eval_res['total_risk'],
                        'timeframe': selected_tf['label'], 'strategy': '200SMA_Fakeout_Short', 'target_timeframe': '20-45 days'
                    })
                    
        return qualifying_setups
    except Exception:
        return []


def fetch_alpha_vantage_fundamentals(ticker):
    """Fetches upcoming earnings dates and news sentiment from Alpha Vantage REST API."""
    import urllib.request
    import json
    
    api_key = "PVB2UDY7AGAA1BOE"
    result = {
        "next_earnings": "Unknown",
        "last_earnings_report": "No recent report data",
        "pullback_reasons": []
    }
    
    # 1. Fetch Earnings
    try:
        url = f"https://www.alphavantage.co/query?function=EARNINGS&symbol={ticker}&apikey={api_key}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            
        quarterly = data.get("quarterlyEarnings", [])
        if quarterly:
            last = quarterly[0]
            result["last_earnings_report"] = f"Reported on {last.get('reportedDate', 'N/A')} | EPS: {last.get('actualEPS', 'N/A')} vs Est: {last.get('estimatedEPS', 'N/A')}"
            
        url_cal = f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&symbol={ticker}&apikey={api_key}"
        req_cal = urllib.request.Request(url_cal, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_cal, timeout=5) as response:
            csv_data = response.read().decode()
            lines = csv_data.strip().split('\n')
            if len(lines) > 1:
                row = lines[1].split(',')
                if len(row) >= 3 and row[0].strip().upper() == ticker.strip().upper():
                    result["next_earnings"] = row[2]
                else:
                    result["next_earnings"] = "Unknown (Rate Limited)"
    except Exception:
        result["next_earnings"] = "N/A"
        
    # 2. Fetch News Sentiment
    try:
        url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={ticker}&limit=3&apikey={api_key}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            
        feed = data.get("feed", [])
        reasons = []
        for article in feed[:3]:
            title = article.get("title", "")
            source = article.get("source", "")
            url_link = article.get("url", "")
            sentiment = article.get("ticker_sentiment", [])
            sentiment_score = "Neutral"
            for t in sentiment:
                if t.get("ticker") == ticker:
                    sentiment_score = t.get("ticker_sentiment_label", "Neutral")
                    break
            reasons.append(f"- **{title}** ({source}) - Sentiment: {sentiment_score} | [Link]({url_link})")
            
        if reasons:
            result["pullback_reasons"] = reasons
        else:
            result["pullback_reasons"] = ["No recent news articles found."]
    except Exception:
         result["pullback_reasons"] = ["Error fetching news or sentiment."]
         
    return result


def get_company_overview(ticker):
    import os
    import json
    import urllib.request
    
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overview_cache.json")
    
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                cache = json.load(f)
        except Exception:
            pass
            
    ticker_upper = ticker.upper().strip()
    if ticker_upper in cache:
        return cache[ticker_upper]
        
    api_key = "PVB2UDY7AGAA1BOE"
    default_overview = {"MarketCapitalization": "0", "Sector": "Unknown"}
    
    try:
        url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker_upper}&apikey={api_key}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            
        if not data or "Information" in data or "Note" in data or "MarketCapitalization" not in data:
            return default_overview
            
        cache[ticker_upper] = data
        try:
            with open(cache_file, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass
            
        return data
    except Exception:
        return default_overview


def run_dsl_screener(strategy_filter="all"):
    import os
    import json
    import pandas as pd
    import functools
    from concurrent.futures import ProcessPoolExecutor

    # Load strategy definitions
    strategies_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategies.json")
    if not os.path.exists(strategies_file):
        return f"Error: strategies.json not found at {strategies_file}."
        
    try:
        with open(strategies_file, "r") as f:
            all_strategies = json.load(f)
    except Exception as e:
        return f"Error loading strategies.json: {str(e)}"
        
    # Filter strategies based on strategy_filter
    active_strategies = []
    if strategy_filter == "all":
        active_strategies = all_strategies
    else:
        filter_names = [n.strip().lower() for n in strategy_filter.split(",")]
        active_strategies = [s for s in all_strategies if s["name"].lower() in filter_names]
        
    if not active_strategies:
        valid_names = [s["name"] for s in all_strategies]
        return f"No matching strategies found for '{strategy_filter}'. Available strategies: {', '.join(valid_names)}"
        
    # Get all active stock tickers from database
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM stocks WHERE type = 'stock' AND volume > 10000")
    tickers = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    valid_trades = []
    
    worker_fn = functools.partial(process_dsl_candidate, active_strategies=active_strategies)
    with ProcessPoolExecutor() as executor:
        results = executor.map(worker_fn, tickers, chunksize=max(1, len(tickers) // 100))
        for r in results:
            if r:
                valid_trades.extend(r)
                
    if not valid_trades:
        return f"Scan completed successfully. No stocks met any of the active strategy criteria: {[s['name'] for s in active_strategies]} and centralized trading rules."
        
    df_trades = pd.DataFrame(valid_trades)
    df_trades = df_trades.sort_values('rr_ratio', ascending=False).reset_index(drop=True)
    
    # Save results locally
    df_trades.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "valid_trades_dsl.csv"), index=False)
    
    # Generate catalyst details and charts for the top 4 setups
    top_setups = df_trades.head(4)
    charts_output = []
    
    for _, trade in top_setups.iterrows():
        ticker = trade['ticker']
        strat_name = trade['strategy']
        target_tf = trade['target_timeframe']
        
        strat_def = next((s for s in active_strategies if s["name"] == strat_name), None)
        trade_type = strat_def["type"] if strat_def else "long"
        
        display_ma = "9ema"
        if "20EMA" in strat_name:
            display_ma = "20ema"
        elif "50SMA" in strat_name:
            display_ma = "50sma"
        elif "200SMA" in strat_name:
            display_ma = "200sma"
        elif "Mean_Reversion" in strat_name:
            display_ma = "bollinger_lower"
            
        draw_trade_chart_with_sr(
            ticker, trade['entry'], trade['sl'], trade['target'], trade['timeframe'], moving_average=display_ma
        )
        zoom_path = os.path.join(ARTIFACTS_DIR, f"{ticker}_trade_sr_chart.png").replace('\\', '/')
        context_path = os.path.join(ARTIFACTS_DIR, f"{ticker}_1y_context.png").replace('\\', '/')
        
        if trade_type == "short":
            potential_profit = (trade['entry'] - trade['target']) * trade['shares']
        else:
            potential_profit = (trade['target'] - trade['entry']) * trade['shares']
            
        profit_pct = (potential_profit / trade['total_alloc']) * 100
        
        fundamentals = fetch_alpha_vantage_fundamentals(ticker)
        news_md = "\n".join(fundamentals["pullback_reasons"])
        
        charts_output.append(
            f"#### **{ticker} {trade_type.capitalize()} Trade Setup ({strat_name.replace('_', ' ')})**\n"
            f"* **Strategy**: {strat_name} ({target_tf} Target Horizon)\n"
            f"* **Entry Price**: ${trade['price']:.2f} | **Stop Loss**: ${trade['sl']:.2f} | **Target**: ${trade['target']:.2f}\n"
            f"* **Risk/Reward**: {trade['rr_ratio']:.2f}x\n"
            f"* **Position Size**: {trade['shares']} shares (Total Cost: ${trade['total_alloc']:.2f})\n"
            f"* **Total Risk**: ${trade['total_risk']:.2f} | **Potential Profit**: ${potential_profit:.2f} ({profit_pct:.2f}%)\n"
            f"* **Fundamental Catalyst & Earnings**:\n"
            f"  * **Next Earnings Date**: {fundamentals['next_earnings']}\n"
            f"  * **Last Earnings**: {fundamentals['last_earnings_report']}\n"
            f"  * **Recent News & Sentiment**:\n"
            f"  {news_md}\n"
            f"* **Charts**: [1-Year Context](file:///{context_path}) | [Zoomed Setup](file:///{zoom_path})"
        )
        
    md_table = df_trades.to_markdown(index=False)
    rules = load_rules()
    charts_section = "\n\n".join(charts_output)
    
    return (
        f"### Dynamic Strategy DSL Screen Results\n"
        f"**Applied Trading Rules**: Portfolio: ${rules['portfolio_size']:.2f} | "
        f"Max Alloc/Trade: {rules['max_allocation_pct']}% | "
        f"Max Risk/Trade: {rules['max_risk_pct']}% | "
        f"Min Risk/Reward: {rules['min_risk_reward_ratio']}x\n\n"
        f"Found **{len(df_trades)}** setups matching strategies. Top candidate analysis:\n\n"
        f"{charts_section}\n\n"
        f"### All Discovered Setups\n"
        f"{md_table}"
    )


@mcp.tool()
def screen_pullback_trades(moving_average: str = "all") -> str:
    """Run the multi-strategy Pullback screener (9 EMA touch, 20 EMA touch, 50 SMA fakeout, 200 SMA fakeout) across all stocks.
    
    Args:
        moving_average: Filter results to a specific moving average ('9ema', '20ema', '50sma', '200sma') or run 'all'. Default is 'all'.
    """
    ma_opt = moving_average.lower().strip()
    if ma_opt == "all":
        filter_str = "9EMA_Touch,20EMA_Touch,50SMA_Fakeout,200SMA_Fakeout"
    elif ma_opt == "9ema":
        filter_str = "9EMA_Touch"
    elif ma_opt == "20ema":
        filter_str = "20EMA_Touch"
    elif ma_opt == "50sma":
        filter_str = "50SMA_Fakeout"
    elif ma_opt == "200sma":
        filter_str = "200SMA_Fakeout"
    else:
        filter_str = "9EMA_Touch,20EMA_Touch,50SMA_Fakeout,200SMA_Fakeout"
        
    return run_dsl_screener(filter_str)


@mcp.tool()
def screen_short_pullback_trades(moving_average: str = "all") -> str:
    """Run the Swing Short Pullback screening strategies across the stock database for downtrending stocks.
    
    Args:
        moving_average: Pullback moving average level to filter by. 
                        Options: '9ema', '20ema', '50sma', '200sma', 'all'. Default is 'all'.
    """
    ma_opt = moving_average.lower().strip()
    if ma_opt == "all":
        filter_str = "9EMA_Touch_Short,20EMA_Touch_Short,50SMA_Fakeout_Short,200SMA_Fakeout_Short"
    elif ma_opt == "9ema":
        filter_str = "9EMA_Touch_Short"
    elif ma_opt == "20ema":
        filter_str = "20EMA_Touch_Short"
    elif ma_opt == "50sma":
        filter_str = "50SMA_Fakeout_Short"
    elif ma_opt == "200sma":
        filter_str = "200SMA_Fakeout_Short"
    else:
        filter_str = "9EMA_Touch_Short,20EMA_Touch_Short,50SMA_Fakeout_Short,200SMA_Fakeout_Short"
        
    return run_dsl_screener(filter_str)


@mcp.tool()
def screen_crossover_trades(moving_average: str = "50sma") -> str:
    """Run the Crossover screening strategy to find stocks crossing a moving average from below to above.
    
    Args:
        moving_average: The moving average to cross. Options: '50sma'. Default is '50sma'.
    """
    return run_dsl_screener("50SMA_Crossover")


@mcp.tool()
def screen_trades(strategy_name: str = "all") -> str:
    """Run the dynamic Strategy DSL Stock Screener using strategy definitions in strategies.json.
    
    Args:
        strategy_name: The name of the strategy to run (e.g. 'Mean_Reversion', '9EMA_Touch'),
                       a list of comma-separated strategy names, or 'all'. Default is 'all'.
    """
    return run_dsl_screener(strategy_name)

@mcp.tool()
def get_etf_holdings(ticker: str) -> str:
    """Retrieve the top holdings, sector weightings, and asset allocation for an ETF from Yahoo Finance.
    
    Args:
        ticker: The ETF symbol (e.g. 'SPY', 'QQQ', 'ASHR').
    """
    import yfinance as yf
    ticker = ticker.upper().strip()
    try:
        etf = yf.Ticker(ticker)
        fund_data = etf.funds_data
        
        if fund_data is None:
            return f"Error: No ETF fund data found for ticker '{ticker}'."
            
        output = [f"## ETF Profile: {ticker}"]
        
        # 1. Top Holdings
        try:
            holdings = fund_data.top_holdings
            if holdings is not None and not holdings.empty:
                output.append("\n### Top 10 Holdings")
                holdings_df = holdings.copy()
                holdings_df['Holding Percent'] = (holdings_df['Holding Percent'] * 100).round(2).astype(str) + '%'
                output.append(holdings_df.to_markdown())
                
                # Cache in database
                try:
                    conn_c = get_connection()
                    cursor_c = conn_c.cursor()
                    cursor_c.execute("CREATE TABLE IF NOT EXISTS etf_holdings (etf_ticker TEXT, stock_ticker TEXT, PRIMARY KEY (etf_ticker, stock_ticker))")
                    for sym in holdings.index.tolist():
                        if sym:
                            cursor_c.execute("INSERT OR IGNORE INTO etf_holdings (etf_ticker, stock_ticker) VALUES (?, ?)", (ticker, str(sym).strip().upper()))
                    conn_c.commit()
                    conn_c.close()
                except Exception:
                    pass
            else:
                output.append("\n*No holdings details available.*")
        except Exception as e:
            output.append(f"\n*Could not retrieve holdings: {e}*")
            
        # 2. Sector Weightings
        try:
            sectors = fund_data.sector_weightings
            if sectors and isinstance(sectors, dict):
                output.append("\n### Sector Weightings")
                sector_data = [{"Sector": k, "Weight": f"{v*100:.2f}%"} for k, v in sectors.items()]
                output.append(pd.DataFrame(sector_data).to_markdown(index=False))
        except Exception as e:
            pass
            
        # 3. Asset Allocation
        try:
            assets = fund_data.asset_classes
            if assets and isinstance(assets, dict):
                output.append("\n### Asset Allocation")
                asset_data = [{"Asset Class": k, "Allocation": f"{v*100:.2f}%"} for k, v in assets.items()]
                output.append(pd.DataFrame(asset_data).to_markdown(index=False))
        except Exception as e:
            pass
            
        return "\n".join(output)
    except Exception as e:
        return f"Error retrieving ETF holdings: {str(e)}"


@mcp.tool()
def find_etfs_holding_stock(ticker: str) -> str:
    """Perform a reverse lookup to find which ETFs hold a given stock ticker in their top holdings.
    
    Args:
        ticker: The stock ticker (e.g. 'AAPL', 'GM').
    """
    ticker = ticker.upper().strip()
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Ensure table exists
        cursor.execute("CREATE TABLE IF NOT EXISTS etf_holdings (etf_ticker TEXT, stock_ticker TEXT, PRIMARY KEY (etf_ticker, stock_ticker))")
        
        cursor.execute("""
            SELECT h.etf_ticker, s.price, s.volume
            FROM etf_holdings h
            JOIN stocks s ON h.etf_ticker = s.ticker
            WHERE h.stock_ticker = ?
            ORDER BY s.volume DESC
        """, (ticker,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return f"No ETFs found holding '{ticker}' in the local database. Try running bootstrap_etf_holdings.py or fetching more ETF profiles."
            
        output = [f"## ETFs Holding {ticker} (Top constituents in local cache)"]
        df_etfs = pd.DataFrame(rows, columns=['ETF Ticker', 'Price ($)', 'Daily Volume'])
        output.append(df_etfs.to_markdown(index=False))
        return "\n".join(output)
    except Exception as e:
        return f"Error executing reverse ETF lookup: {str(e)}"

@mcp.tool()
def generate_trade_plan(ticker: str) -> str:
    """Generate a comprehensive trade plan for a stock following 10 quantitative trading rules.
    
    Checks risk/reward feasibility (1:3 target vs resistance), sets entry ranges, stop loss,
    profit-taking scale-outs, daily ATR comparison, timeframes, earnings dates, industry trends,
    and options hedging guidelines.
    
    Args:
        ticker: The stock symbol (e.g. 'GM', 'BKR', 'HON').
    """
    import yfinance as yf
    ticker = ticker.upper().strip()
    
    try:
        # Step 1: Connect to DB and fetch price/indicative info
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.price, s.volatility_20, s.high_52w, s.low_52w, s.filepath, c.sector, c.industry
            FROM stocks s
            LEFT JOIN company_info c ON s.ticker = c.ticker
            WHERE s.ticker = ?
        """, (ticker,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return f"Error: Ticker '{ticker}' not found in the database. Please run indexer.py first."
            
        current_price, hist_vol, high_52w, low_52w, filepath, sector, industry = row
            
        # Step 2: Calculate 14-day ATR from database history
        df = load_history_df(ticker, 150)
        if df is None:
            return f"Error: No historical data found for {ticker}."
        # Ensure date is string type and vol is present if needed
        df['date'] = df['date'].dt.strftime('%Y%m%d')
        
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr_14 = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
        
        # Step 3: Find Support & Resistance Levels (extrema detection lookback 150 days)
        df_sr = df.tail(150).copy().reset_index(drop=True)
        window = 10
        df_sr['is_min'] = df_sr['low'] == df_sr['low'].rolling(window*2+1, center=True).min()
        df_sr['is_max'] = df_sr['high'] == df_sr['high'].rolling(window*2+1, center=True).max()
        
        raw_mins = df_sr[df_sr['is_min']]['low'].tolist()
        raw_maxs = df_sr[df_sr['is_max']]['high'].tolist()
        
        def cluster_levels(levels, tolerance=0.015):
            if not levels:
                return []
            levels = sorted(levels)
            clusters = []
            curr_c = [levels[0]]
            for val in levels[1:]:
                avg = sum(curr_c) / len(curr_c)
                if (val - avg) / avg <= tolerance:
                    curr_c.append(val)
                else:
                    clusters.append(curr_c)
                    curr_c = [val]
            clusters.append(curr_c)
            return [round(sum(c)/len(c), 2) for c in clusters]
            
        supports = sorted([p for p in cluster_levels(raw_mins) if p < current_price], reverse=True)
        resistances = sorted([p for p in cluster_levels(raw_maxs) if p > current_price])
        
        # Determine strong levels
        strong_support = supports[0] if supports else round(current_price - 2 * atr_14, 2)
        strong_resistance = resistances[0] if resistances else round(high_52w, 2)
        
        # Step 4: Establish Entry, Stop Loss, and Targets
        # Entry Range
        entry_low = round(min(current_price, strong_support + 0.1 * atr_14), 2)
        entry_high = round(current_price + 0.15 * atr_14, 2)
        entry_mid = round((entry_low + entry_high) / 2, 2)
        
        # Stop Loss (placed just below the strong support)
        stop_loss = round(strong_support - 0.25 * atr_14, 2)
        if stop_loss >= current_price:
            stop_loss = round(current_price - 2.0 * atr_14, 2)
            
        risk = round(entry_mid - stop_loss, 2)
        
        # Target Prices
        target_1_3 = round(entry_mid + 3 * risk, 2)
        target_1_2 = round(entry_mid + 2 * risk, 2)
        target_1_1 = round(entry_mid + 1 * risk, 2)
        
        # Step 5: Check ATR Proximity (Rule 6)
        atr_warning = ""
        if risk < 1.2 * atr_14:
            atr_warning = (
                f"\n> [!WARNING]\n"
                f"> **Stop Loss is too tight relative to daily volatility (ATR)!**\n"
                f"> Your risk spacing (${risk:.2f}) is less than 1.2x ATR (${1.2 * atr_14:.2f}). "
                f"Daily noise may trigger your stop prematurely. Consider widening your stop to at least ${entry_mid - 2*atr_14:.2f}."
            )
            
        # Step 6: Risk/Reward Feasibility (Rule 1)
        rr_commentary = ""
        chosen_target = target_1_3
        chosen_rr = "1:3"
        
        if target_1_3 > strong_resistance:
            # 1:3 Target is blocked by resistance
            rr_commentary = (
                f"\n> [!NOTE]\n"
                f"> **1:3 Risk/Reward target (${target_1_3:.2f}) is blocked by major overhead resistance at ${strong_resistance:.2f}!**\n"
                f"> *Alternative Option*: Consider a **1:2 R/R target (${target_1_2:.2f})** or **1:1 R/R target (${target_1_1:.2f})** "
                f"which sit below the resistance ceiling."
            )
            if target_1_2 <= strong_resistance:
                chosen_target = target_1_2
                chosen_rr = "1:2 (Modified due to resistance)"
            else:
                chosen_target = target_1_1
                chosen_rr = "1:1 (Modified due to resistance)"
        else:
            rr_commentary = f"\n> [!TIP]\n> **1:3 Risk/Reward is highly feasible.** The target (${target_1_3:.2f}) sits below the major resistance ceiling at ${strong_resistance:.2f}."
            
        # Step 7: Timeframe Estimation (Rule 7)
        # Assuming stock moves average of 0.6 * ATR towards target or stop daily
        import math
        days_to_stop = max(1, int(math.ceil(risk / (0.6 * atr_14))))
        days_to_target = max(1, int(math.ceil((chosen_target - entry_mid) / (0.6 * atr_14))))
        timeframe_str = f"{days_to_stop} to {days_to_target} trading days"
        
        # Step 8: Upcoming Events / Earnings (Rule 8)
        earnings_date = "N/A"
        earnings_warning = ""
        try:
            stock_yf = yf.Ticker(ticker)
            cal = stock_yf.calendar
            if cal is not None and not cal.empty:
                dates = cal.get('Earnings Date')
                if dates is not None:
                    earnings_date = ", ".join([d.strftime('%Y-%m-%d') for d in dates])
                    # Check if within 7 days
                    from datetime import datetime, timedelta
                    today = datetime.now()
                    for d in dates:
                        # timezone naive comparison
                        d_naive = d.replace(tzinfo=None)
                        if abs((d_naive - today).days) <= 7:
                            earnings_warning = (
                                f"\n> [!CAUTION]\n"
                                f"> **HIGH RISK BINARY EVENT**: Earnings announcement is scheduled for **{earnings_date}** (within 7 days). "
                                f"Standard stop losses may be bypassed by overnight price gaps. Avoid new directional trades or hedge with options."
                            )
                            break
        except Exception:
            pass
            
        # Step 9: Industry Peer Trend Correlation (Rule 9)
        peer_trend_str = "No peer data available."
        if sector and industry:
            try:
                conn_p = get_connection()
                cur_p = conn_p.cursor()
                cur_p.execute("""
                    SELECT AVG(s.return_5d) as avg_5d
                    FROM stocks s
                    JOIN company_info c ON s.ticker = c.ticker
                    WHERE c.industry = ? AND s.ticker != ?
                """, (industry, ticker))
                avg_5d = cur_p.fetchone()[0]
                conn_p.close()
                if avg_5d is not None:
                    peer_trend_str = f"Peers in the '{industry}' industry average **{avg_5d:+.2f}%** return over the last 5 trading days."
            except Exception:
                pass
                
        # Step 10: Option Hedging Recommendation (Rule 10)
        options_strategy = "No options recommendation available."
        try:
            stock_yf = yf.Ticker(ticker)
            expirations = stock_yf.options
            if expirations:
                # Suggest hedging strikes
                options_strategy = (
                    f"GM has active options (closest expiration: {expirations[0]}).\n"
                    f"  * **Hedging Recommendation**: "
                )
                if earnings_warning:
                    options_strategy += (
                        f"Due to the upcoming earnings event, implement a **Protective Collar**:\n"
                        f"    - Buy Stock at current market price.\n"
                        f"    - Buy a Protective Put (Strike: ${strong_support:.0f} near your Stop Loss).\n"
                        f"    - Sell a Covered Call (Strike: ${chosen_target:.0f} near your target) to offset put premium."
                    )
                else:
                    options_strategy += (
                        f"Implement a **Protective Put** by buying the Stock and buying the Put (Strike: ${strong_support:.0f}) "
                        f"to protect against black swan market declines while keeping unlimited upside."
                    )
            else:
                options_strategy = "This asset does not support standard listed options contracts. Use strict stop loss orders."
        except Exception:
            pass
            
        # Compile Output
        output = [
            f"## Quantitative Trade Plan: {ticker}",
            f"* **Current Price**: ${current_price:.2f} | **52w Range**: ${low_52w:.2f} - ${high_52w:.2f}",
            f"* **14-day ATR**: ${atr_14:.2f} | **Historical 20d Volatility**: {hist_vol:.1f}%",
            f"* **Industry Trend**: {peer_trend_str}\n",
            f"### Trade Levels",
            f"1. **Entry Range**: **`${entry_low:.2f}` to `${entry_high:.2f}`** (Target mid-point: `${entry_mid:.2f}`)",
            f"2. **Stop Loss**: **`${stop_loss:.2f}`** (Aligned below support at `${strong_support:.2f}`)",
            f"3. **Profit Target (Final)**: **`${chosen_target:.2f}`** ({chosen_rr} R/R)",
            f"4. **Profit Scale-Out (Rule 4)**: Scale out **50% of position at `${target_1_1:.2f}`** (1:1 R/R) and move stop to break-even. Scale out the remaining **50% at `${chosen_target:.2f}`**.",
            f"5. **Estimated Timeframe**: **`{timeframe_str}`** (calculated via ATR momentum)\n",
            f"### Technical Alignment & Risks",
            f"* **Support Levels**: {', '.join([f'${s}' for s in supports[:3]]) if supports else 'None'}",
            f"* **Resistance Levels**: {', '.join([f'${r}' for r in resistances[:3]]) if resistances else 'None'}",
            atr_warning,
            rr_commentary,
            earnings_warning,
            f"\n### Options & Hedging Analysis",
            f"* {options_strategy}"
        ]
        
        return "\n".join(output)
    except Exception as e:
        return f"Error generating trade plan: {str(e)}"


@mcp.tool()
def get_sector_rotation(days: int = 5) -> str:
    """Analyze sector rotation using the 11 Select Sector SPDR ETFs over a specified timeframe.
    
    Generates a horizontal bar chart ranking the sector returns and returns a markdown image link.
    
    Args:
        days: Timeframe to analyze in trading days. Options: 1, 5, 20, 250. Default is 5.
    """
    days_map = {
        1: 'return_1d',
        5: 'return_5d',
        20: 'return_20d',
        250: 'return_250d'
    }
    
    if days not in days_map:
        return f"Error: Invalid timeframe '{days}' days. Supported timeframes are: 1, 5, 20, 250."
        
    col_name = days_map[days]
    
    # Sector names mapping for clarity
    sector_names = {
        'XLK': 'Technology',
        'XLY': 'Consumer Discretionary',
        'XLC': 'Communication Services',
        'XLF': 'Financials',
        'XLI': 'Industrials',
        'XLV': 'Healthcare',
        'XLP': 'Consumer Staples',
        'XLE': 'Energy',
        'XLB': 'Materials',
        'XLU': 'Utilities',
        'XLRE': 'Real Estate'
    }
    
    tickers_str = ", ".join([f"'{t}'" for t in sector_names.keys()])
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT ticker, price, {col_name}
            FROM stocks
            WHERE ticker IN ({tickers_str})
            ORDER BY {col_name} DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "Error: No Sector ETF performance data found."
            
        data = []
        for ticker, price, ret in rows:
            data.append({
                'Sector ETF': ticker,
                'Sector Name': sector_names.get(ticker, 'Unknown'),
                'Price ($)': round(price, 2),
                'Return (%)': f"{ret:+.2f}%" if ret is not None else "N/A",
                'raw_return': ret if ret is not None else -999.0
            })
            
        df = pd.DataFrame(data)
        
        # Build analysis commentary
        top_sector = df.iloc[0]
        bottom_sector = df.iloc[-1]
        
        # Group sectors into categories
        growth_perf = []
        cyclical_perf = []
        defensive_perf = []
        
        for idx, row in df.iterrows():
            ticker = row['Sector ETF']
            raw_ret = row['raw_return']
            if raw_ret == -999.0:
                continue
            if ticker in ['XLK', 'XLY', 'XLC']:
                growth_perf.append(raw_ret)
            elif ticker in ['XLF', 'XLI', 'XLB', 'XLE']:
                cyclical_perf.append(raw_ret)
            elif ticker in ['XLV', 'XLP', 'XLU', 'XLRE']:
                defensive_perf.append(raw_ret)
                
        avg_growth = sum(growth_perf) / len(growth_perf) if growth_perf else 0.0
        avg_cyclical = sum(cyclical_perf) / len(cyclical_perf) if cyclical_perf else 0.0
        avg_defensive = sum(defensive_perf) / len(defensive_perf) if defensive_perf else 0.0
        
        # Generate chart
        try:
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(12, 7))
            
            # Sort values ascending for horizontal bar chart (top performer at the top)
            df_sorted = df.sort_values('raw_return', ascending=True).copy()
            
            colors = ['#2ea44f' if x >= 0 else '#d73a49' for x in df_sorted['raw_return']]
            
            labels = df_sorted['Sector Name']
            bars = ax.barh(labels, df_sorted['raw_return'], color=colors, edgecolor='none')
            
            # Add labels to the ends of the bars
            for bar in bars:
                width = bar.get_width()
                label_text = f"{width:+.2f}%"
                if width >= 0:
                    ax.text(width + 0.1, bar.get_y() + bar.get_height()/2, label_text, 
                            va='center', ha='left', color='#8b949e', weight='bold', fontsize=10)
                else:
                    ax.text(width - 0.1, bar.get_y() + bar.get_height()/2, label_text, 
                            va='center', ha='right', color='#8b949e', weight='bold', fontsize=10)
                            
            ax.set_title(f'Sector Rotation: {days}-Day Performance Leaderboard', fontsize=14, color='#58a6ff', weight='bold', pad=15)
            ax.set_xlabel('Return (%)', fontsize=11, color='#8b949e')
            ax.grid(True, color='#21262d', linestyle=':', alpha=0.6, axis='x')
            ax.tick_params(colors='#8b949e')
            
            # Adjust margins and limits to avoid clipping
            min_ret = df_sorted['raw_return'].min()
            max_ret = df_sorted['raw_return'].max()
            ax.set_xlim(min_ret - abs(min_ret)*0.2 - 0.5, max_ret + abs(max_ret)*0.2 + 0.5)
            
            # Save chart to local charts folder
            charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
            os.makedirs(charts_dir, exist_ok=True)
            chart_path = os.path.join(charts_dir, f"sector_rotation_{days}d.png")
            plt.tight_layout()
            plt.savefig(chart_path, dpi=150, facecolor='#0d1117')
            plt.close()
            
            url_path = chart_path.replace('\\', '/')
            img_md = f"\n### Visual Performance Chart\n![Sector Rotation Chart](file:///{url_path})\n"
        except Exception as e:
            img_md = f"\n*Could not generate chart: {e}*\n"
            
        commentary = []
        commentary.append(f"## Sector Rotation Analysis ({days}-Day Performance Ranking)")
        commentary.append(f"Analyzing the 11 Select Sector SPDR ETFs to track institutional money flows.")
        
        commentary.append(img_md)
        
        commentary.append("\n### Sector Performance Leaderboard")
        commentary.append(df[['Sector ETF', 'Sector Name', 'Price ($)', 'Return (%)']].to_markdown(index=False))
        
        commentary.append("\n### Money Flow Summary")
        commentary.append(f"* **Top Leading Sector**: **{top_sector['Sector Name']} ({top_sector['Sector ETF']})** returning **{top_sector['Return (%)']}**")
        commentary.append(f"* **Worst Lagging Sector**: **{bottom_sector['Sector Name']} ({bottom_sector['Sector ETF']})** returning **{bottom_sector['Return (%)']}**")
        
        commentary.append("\n### Institutional Category Averages")
        commentary.append(f"* **Defensive / Value Sectors** (XLV, XLP, XLU, XLRE): **{avg_defensive:+.2f}%**")
        commentary.append(f"* **Cyclical / Value Sectors** (XLF, XLI, XLB, XLE): **{avg_cyclical:+.2f}%**")
        commentary.append(f"* **Growth / Tech Sectors** (XLK, XLY, XLC): **{avg_growth:+.2f}%**")
        
        # Interpretive rotation message
        rotation_theme = ""
        if avg_defensive > avg_growth and avg_cyclical > avg_growth:
            rotation_theme = "Defensive Risk-Off Rotation (Institutional money is fleeing High-Beta Tech/Growth and hiding in Commodities, Energy, Real Estate, and defensive Staples/Utilities)."
        elif avg_growth > avg_defensive and avg_growth > avg_cyclical:
            rotation_theme = "Risk-On Expansion Rotation (Growth and technology are leading, indicating appetite for speculation and market expansion)."
        else:
            rotation_theme = "Mixed / Consolidation Rotation (No clear growth vs defensive trend, sector-specific dynamics are dominating flows)."
            
        commentary.append(f"\n> [!NOTE]\n> **Rotation Theme**: *{rotation_theme}*")
        
        return "\n".join(commentary)
    except Exception as e:
        return f"Error executing sector rotation analysis: {str(e)}"


@mcp.tool()
def import_historical_data_from_zip(zip_path: str) -> str:
    """Unzip historical Stooq data and load all daily price rows into the SQLite database.
    
    This updates the historical_prices table and re-calculates indicators for all stocks.
    The temporary unzipped files are automatically cleaned up afterward.
    
    Args:
        zip_path: Absolute path to the d_us_txt (1).zip file on disk.
    """
    if not os.path.exists(zip_path):
        return f"Error: ZIP file not found at '{zip_path}'."
        
    import shutil
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_stooq_extract")
    
    try:
        # Re-route EXTRACT_DIR to temp_dir dynamically
        global EXTRACT_DIR
        old_extract_dir = EXTRACT_DIR
        EXTRACT_DIR = temp_dir
        
        print(f"Extracting {zip_path} to {temp_dir}...")
        extract_zip(zip_path, temp_dir)
        
        print("Loading historical prices into database...")
        load_historical_prices()
        
        print("Running indexing to calculate latest technical indicators...")
        run_indexing()
        
        # Restore EXTRACT_DIR
        EXTRACT_DIR = old_extract_dir
        
        # Cleanup temporary files
        print("Cleaning up temporary extracted files...")
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("Cleaned up successfully!")
        
        return "Data import and indexing completed successfully! Text files cleaned up from disk."
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return f"Error running data import: {str(e)}"


@mcp.tool()
def import_5min_data_from_zip(zip_path: str) -> str:
    """Stream and load historical 5-minute price records directly from a Stooq ZIP file (e.g. 5_us_txt.zip).
    
    This updates the historical_prices_5min table in screener.db and rebuilds the query index.
    
    Args:
        zip_path: Absolute path to the 5_us_txt.zip file on disk.
    """
    if not os.path.exists(zip_path):
        return f"Error: ZIP file not found at '{zip_path}'."
        
    try:
        total = stream_load_historical_prices_5min(zip_path)
        if total:
            return f"5-minute data import completed successfully! Loaded {total:,} records."
        else:
            return "Data import failed (no files found in ZIP)."
    except Exception as e:
        return f"Error running 5-minute data import: {str(e)}"


if __name__ == "__main__":
    mcp.run()


@mcp.tool()
def bootstrap_etf_holdings() -> str:
    """Bootstrap ETF holdings metadata from Yahoo Finance into the etf_holdings table.
    
    This queries active ETFs, downloads their top holdings, and populates the database.
    """
    import sqlite3
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time
    
    # Create table if not exists
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS etf_holdings (
            etf_ticker TEXT,
            stock_ticker TEXT,
            PRIMARY KEY (etf_ticker, stock_ticker)
        )
    """)
    conn.commit()
    
    # Get active ETFs
    cursor.execute("SELECT ticker FROM stocks WHERE type = 'etf' AND volume > 10000 ORDER BY volume DESC")
    etfs = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    if not etfs:
        return "No active ETFs found in the database. Please import price data and index first."
        
    print(f"Found {len(etfs)} high-volume ETFs to bootstrap.")
    
    def fetch_etf_holdings(etf_ticker):
        try:
            etf = yf.Ticker(etf_ticker)
            fund_data = etf.funds_data
            if fund_data is None:
                return etf_ticker, []
            holdings = fund_data.top_holdings
            if holdings is None or holdings.empty:
                return etf_ticker, []
            symbols = [str(sym).strip().upper() for sym in holdings.index.tolist() if sym]
            return etf_ticker, symbols
        except Exception:
            return etf_ticker, []
            
    def save_holdings(etf_ticker, symbols):
        if not symbols:
            return
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        for sym in symbols:
            cursor.execute("INSERT OR IGNORE INTO etf_holdings (etf_ticker, stock_ticker) VALUES (?, ?)", (etf_ticker, sym))
        conn.commit()
        conn.close()
        
    start_time = time.time()
    completed = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_etf_holdings, ticker): ticker for ticker in etfs}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                etf_ticker, symbols = future.result()
                if symbols:
                    save_holdings(etf_ticker, symbols)
                    completed += 1
            except Exception as e:
                print(f"Error fetching {ticker}: {e}")
                
    elapsed = time.time() - start_time
    return f"Successfully bootstrapped holdings for {completed}/{len(etfs)} ETFs in {elapsed:.2f} seconds."


@mcp.tool()
def download_and_store_fundamentals() -> str:
    """Download company metadata from Nasdaq API and calculate Betas relative to SPY.
    
    This populates the company_info table in screener.db.
    """
    import requests
    import sqlite3
    import numpy as np
    from concurrent.futures import ProcessPoolExecutor
    import time
    
    # 1. Download fundamental info from Nasdaq API
    print("Downloading company fundamentals from Nasdaq API...")
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    nasdaq_data = {}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            json_data = response.json()
            rows = json_data['data']['rows']
            print(f"Successfully downloaded info for {len(rows)} companies.")
            for row in rows:
                sym = row['symbol'].upper()
                mcap_str = row.get('marketCap', '0')
                try:
                    mcap = float(mcap_str) if mcap_str else 0.0
                except ValueError:
                    mcap = 0.0
                
                ipo_str = row.get('ipoyear', '')
                try:
                    ipo = int(ipo_str) if ipo_str and ipo_str.isdigit() else None
                except ValueError:
                    ipo = None
                    
                nasdaq_data[sym] = {
                    'name': row.get('name', ''),
                    'market_cap': mcap,
                    'country': row.get('country', ''),
                    'ipo_year': ipo,
                    'sector': row.get('sector', 'N/A'),
                    'industry': row.get('industry', 'N/A')
                }
        else:
            print(f"Error: Nasdaq API returned status code {response.status_code}.")
    except Exception as e:
        print(f"Error calling Nasdaq API: {e}.")
        
    # 2. Load SPY returns from database
    spy_df = load_history_df('SPY', 300)
    if spy_df is None or len(spy_df) < 30:
        return "Error: SPY price history not found in database. Please import price data first."
        
    spy_df = spy_df.sort_values('date').reset_index(drop=True)
    spy_df['return'] = spy_df['close'].pct_change()
    spy_ret = spy_df.set_index('date')['return'].dropna().tail(250)
    
    # 3. Fetch all active stock tickers
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM stocks WHERE type = 'stock'")
    tickers = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    print(f"Calculating Betas for {len(tickers)} stocks relative to SPY in parallel...")
    start_time = time.time()
    
    betas = {}
    import functools
    worker_fn = functools.partial(calculate_stock_beta_worker, spy_ret=spy_ret)
    
    with ProcessPoolExecutor() as executor:
        chunksize = max(1, len(tickers) // 100)
        jobs = executor.map(worker_fn, tickers, chunksize=chunksize)
        
        for res in jobs:
            if res is not None:
                ticker, beta = res
                if beta is not None:
                    betas[ticker] = beta
                
    print(f"Calculated Betas in {time.time() - start_time:.2f} seconds.")
    
    # 4. Save to company_info table
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS company_info (
        ticker TEXT PRIMARY KEY,
        name TEXT,
        market_cap REAL,
        country TEXT,
        ipo_year INTEGER,
        sector TEXT,
        industry TEXT,
        beta REAL
    )
    """)
    
    insert_data = []
    for ticker in tickers:
        info = nasdaq_data.get(ticker, {
            'name': '',
            'market_cap': 0.0,
            'country': '',
            'ipo_year': None,
            'sector': 'N/A',
            'industry': 'N/A'
        })
        beta = betas.get(ticker, None)
        insert_data.append((
            ticker,
            info['name'],
            info['market_cap'],
            info['country'],
            info['ipo_year'],
            info['sector'],
            info['industry'],
            beta
        ))
        
    cur.executemany("""
    INSERT OR REPLACE INTO company_info (ticker, name, market_cap, country, ipo_year, sector, industry, beta)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, insert_data)
    conn.commit()
    conn.close()
    
    return f"Successfully populated company_info for {len(insert_data)} tickers with Betas and fundamental info!"

import os
import sys
import sqlite3
import zipfile
import time
import glob

DB_PATH = "screener.db"
DOWNLOADS_DIR = os.path.expanduser(r"~\Downloads")

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

def find_latest_5min_zip():
    crdownloads = glob.glob(os.path.join(DOWNLOADS_DIR, "*.crdownload"))
    if crdownloads:
        print(f"Notice: A browser download is currently active ({[os.path.basename(f) for f in crdownloads]}).")
        return None, True

    candidates = glob.glob(os.path.join(DOWNLOADS_DIR, "*5_us_txt*.zip"))
    if not candidates:
        return None, False

    candidates.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return candidates[0], False

def clean_and_stream_load_5min(zip_path):
    start_time = time.time()
    
    print(f"Opening ZIP archive: {zip_path}...")
    z = zipfile.ZipFile(zip_path, 'r')
    
    # Identify relevant 5-minute files
    txt_files = [f for f in z.namelist() if f.endswith('.txt') and 'data/5 min/us' in f.lower().replace('\\', '/')]
    if not txt_files:
        # Fallback to any txt inside zip
        txt_files = [f for f in z.namelist() if f.endswith('.txt')]
        
    total_files = len(txt_files)
    print(f"Found {total_files:,} stock/ETF 5-minute files in archive.")
    if total_files == 0:
        print("Error: No data files found in ZIP.")
        return False
        
    # Connect to SQLite
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Enable maximum write performance
    cursor.execute("PRAGMA synchronous = OFF")
    cursor.execute("PRAGMA journal_mode = MEMORY")
    cursor.execute("PRAGMA cache_size = -100000")  # ~100MB RAM cache
    
    print("Dropping existing historical_prices_5min table and index to clean database...")
    cursor.execute("DROP TABLE IF EXISTS historical_prices_5min")
    cursor.execute("DROP INDEX IF EXISTS idx_hist_5min_ticker_date_time")
    conn.commit()
    print("Database cleaned successfully.")
    
    print("Creating clean historical_prices_5min table...")
    cursor.execute("""
        CREATE TABLE historical_prices_5min (
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
    
    # Metadata registration
    print("Extracting ticker metadata...")
    ticker_metadata = []
    for filepath in txt_files:
        ticker, exchange, asset_type = parse_path_info_5min(filepath)
        ticker_metadata.append((ticker, exchange, asset_type))
        
    print("Upserting ticker metadata to stocks table...")
    cursor.executemany(
        "INSERT OR IGNORE INTO stocks (ticker, exchange, type) VALUES (?, ?, ?)", 
        ticker_metadata
    )
    conn.commit()
    
    print("\nStreaming and parsing 5-minute data directly from ZIP...")
    insert_sql = "INSERT OR REPLACE INTO historical_prices_5min (ticker, date, time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    
    batch = []
    batch_size = 500000
    total_inserted = 0
    files_processed = 0
    t_start_parse = time.time()
    
    for filepath in txt_files:
        # Extract ticker from filename
        filename = filepath.replace('\\', '/').split('/')[-1]
        ticker = filename.split('.')[0].upper()
        if ticker.endswith(".US"):
            ticker = ticker[:-3]
            
        try:
            raw_bytes = z.read(filepath)
            # Fast ASCII decode ignoring malformed characters
            content = raw_bytes.decode('ascii', errors='ignore')
            lines = content.splitlines()
            
            # Skip header line: <TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>
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
            
        files_processed += 1
        
        if len(batch) >= batch_size:
            cursor.executemany(insert_sql, batch)
            conn.commit()
            total_inserted += len(batch)
            elapsed = time.time() - t_start_parse
            rate = total_inserted / elapsed if elapsed > 0 else 0
            print(f"[{files_processed:,}/{total_files:,} files] Inserted {total_inserted:,} records ({rate:,.0f} rec/s)...")
            batch = []
            
    # Insert any remaining records in final batch
    if batch:
        cursor.executemany(insert_sql, batch)
        conn.commit()
        total_inserted += len(batch)
        print(f"[{files_processed:,}/{total_files:,} files] Final batch: Inserted {total_inserted:,} total records.")
        batch = []
        
    z.close()
    
    # Create compound B-Tree index for ultra-fast query performance
    print("\nBuilding database index: idx_hist_5min_ticker_date_time ON historical_prices_5min(ticker, date DESC, time DESC)...")
    idx_start = time.time()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hist_5min_ticker_date_time ON historical_prices_5min(ticker, date DESC, time DESC)")
    conn.commit()
    print(f"Index built successfully in {time.time() - idx_start:.2f} seconds.")
    
    # Verification query
    print("Verifying database contents...")
    cursor.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM historical_prices_5min")
    row = cursor.fetchone()
    conn.close()
    
    total_elapsed = time.time() - start_time
    print("\n" + "=" * 55)
    print("DATABASE CLEAN & 5-MIN RELOAD COMPLETED SUCCESSFULLY")
    print("=" * 55)
    print(f"  - Total Rows:       {row[0]:,}")
    print(f"  - Distinct Tickers: {row[1]:,}")
    print(f"  - Date Range:       {row[2]} -> {row[3]}")
    print(f"  - Total Duration:   {total_elapsed:.2f} seconds ({total_elapsed/60:.2f} minutes)")
    print("=" * 55)
    return True

def main():
    if len(sys.argv) > 1:
        zip_path = sys.argv[1]
    else:
        zip_path, is_downloading = find_latest_5min_zip()
        if is_downloading:
            print("Download is still in progress. Please wait for it to finish.")
            sys.exit(2)
        if not zip_path:
            print("Error: No 5_us_txt*.zip file found in Downloads.")
            sys.exit(1)

    if not os.path.exists(zip_path):
        print(f"Error: ZIP file not found at '{zip_path}'.")
        sys.exit(1)

    print(f"Selected ZIP file: {zip_path}")
    print(f"File size: {os.path.getsize(zip_path):,} bytes ({os.path.getsize(zip_path)/1024/1024:.2f} MB)")
    
    success = clean_and_stream_load_5min(zip_path)
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()

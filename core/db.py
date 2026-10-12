import sqlite3
import json
import os
from typing import List, Dict, Any, Optional
import config

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or config.ACTIVE_DB
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Fast read optimizations
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA cache_size = -32000;") # 32MB cache
    return conn

def get_universe_tickers(universe: str, custom_tickers: Optional[str] = None) -> List[str]:
    u = universe.lower().strip()
    
    if u in ['high_edge_30', 'curated_30']:
        return list(config.HIGH_EDGE_30)
        
    if u == 'custom' and custom_tickers:
        cleaned = [t.strip().upper() for t in custom_tickers.replace(',', ' ').split() if t.strip()]
        return cleaned

    # Load from database / JSON files
    conn = get_connection()
    cur = conn.cursor()
    
    if u in ['dow', 'dow30', 'dow_jones']:
        cur.execute("SELECT ticker FROM company_info WHERE in_dow30 = 1 ORDER BY ticker")
        rows = [r['ticker'] for r in cur.fetchall()]
        if rows: return rows
        # Fallback to json
        json_path = os.path.join(config.DATA_DIR, "dow30_in_db.json")
        if not os.path.exists(json_path):
            json_path = r"C:\Users\saiba\.gemini\antigravity\scratch\dow30_in_db.json"
        if os.path.exists(json_path):
            with open(json_path) as f: return json.load(f)
        return ['AAPL', 'AMGN', 'AXP', 'BA', 'CAT', 'CRM', 'CSCO', 'CVX', 'DIS', 'GS', 'HD', 'HON', 'IBM', 'JNJ', 'JPM', 'KO', 'MCD', 'MMM', 'MRK', 'MSFT', 'NKE', 'NVDA', 'PG', 'TRV', 'UNH', 'V', 'WMT']

    if u in ['nasdaq', 'nasdaq100', 'ndx']:
        cur.execute("SELECT ticker FROM company_info WHERE in_nasdaq100 = 1 ORDER BY ticker")
        rows = [r['ticker'] for r in cur.fetchall()]
        if rows: return rows
        
    if u in ['sp', 'sp500', 's&p', 's&p 500']:
        cur.execute("SELECT ticker FROM company_info WHERE in_sp500 = 1 ORDER BY ticker")
        rows = [r['ticker'] for r in cur.fetchall()]
        if rows: return rows
        json_path = os.path.join(config.DATA_DIR, "sp500_in_db.json")
        if not os.path.exists(json_path):
            json_path = r"C:\Users\saiba\.gemini\antigravity\scratch\sp500_in_db.json"
        if os.path.exists(json_path):
            with open(json_path) as f: return json.load(f)

    # Default to all available tickers in company_info
    cur.execute("SELECT ticker FROM company_info ORDER BY ticker")
    return [r['ticker'] for r in cur.fetchall()]

def get_company_metadata() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT ticker, name, sector, industry, market_cap, beta FROM company_info")
    return {
        r['ticker']: {
            'name': r['name'] or r['ticker'],
            'sector': r['sector'] or 'Other',
            'industry': r['industry'] or 'Other',
            'market_cap': r['market_cap'],
            'beta': r['beta']
        }
        for r in cur.fetchall()
    }

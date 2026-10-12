import sqlite3
import json
import os
from typing import List, Dict, Any, Optional
import config

def get_connection(db_path: Optional[str] = None) -> Optional[sqlite3.Connection]:
    path = db_path or config.ACTIVE_DB
    if not path or not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON;")
        conn.execute("PRAGMA cache_size = -32000;") # 32MB cache
        return conn
    except Exception:
        return None

def _load_json_tickers(filename: str) -> List[str]:
    paths = [
        os.path.join(config.DATA_DIR, filename),
        os.path.join(config.BASE_DIR, "data", filename),
        os.path.join(r"C:\Users\saiba\.gemini\antigravity\scratch\mcp-stock-screener\data", filename),
        os.path.join(r"C:\Users\saiba\.gemini\antigravity\scratch", filename),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return []

def get_universe_tickers(universe: str, custom_tickers: Optional[str] = None) -> List[str]:
    u = universe.lower().strip()
    
    if u in ['high_edge_30', 'curated_30']:
        return list(config.HIGH_EDGE_30)
        
    if u == 'custom' and custom_tickers:
        cleaned = [t.strip().upper() for t in custom_tickers.replace(',', ' ').split() if t.strip()]
        return cleaned

    conn = get_connection()
    cur = conn.cursor() if conn else None
    
    if u in ['dow', 'dow30', 'dow_jones']:
        if cur:
            try:
                cur.execute("SELECT ticker FROM company_info WHERE in_dow30 = 1 ORDER BY ticker")
                rows = [r['ticker'] for r in cur.fetchall()]
                if rows: return rows
            except Exception:
                pass
        dow_json = _load_json_tickers("dow30_in_db.json")
        if dow_json: return dow_json
        return ['AAPL', 'AMGN', 'AXP', 'BA', 'CAT', 'CRM', 'CSCO', 'CVX', 'DIS', 'DOW', 'GS', 'HD', 'HON', 'IBM', 'INTC', 'JNJ', 'JPM', 'KO', 'MCD', 'MMM', 'MRK', 'MSFT', 'NKE', 'NVDA', 'PG', 'TRV', 'UNH', 'V', 'WMT']

    if u in ['nasdaq', 'nasdaq100', 'ndx']:
        if cur:
            try:
                cur.execute("SELECT ticker FROM company_info WHERE in_nasdaq100 = 1 ORDER BY ticker")
                rows = [r['ticker'] for r in cur.fetchall()]
                if rows: return rows
            except Exception:
                pass
        nasdaq_json = _load_json_tickers("nasdaq100_in_db.json")
        if nasdaq_json: return nasdaq_json

    if u in ['sp', 'sp500', 's&p', 's&p 500']:
        if cur:
            try:
                cur.execute("SELECT ticker FROM company_info WHERE in_sp500 = 1 ORDER BY ticker")
                rows = [r['ticker'] for r in cur.fetchall()]
                if rows: return rows
            except Exception:
                pass
        sp_json = _load_json_tickers("sp500_in_db.json")
        if sp_json: return sp_json

    if u in ['scope_all', 'all_scope', 'combined', 'indices_all']:
        # Union of S&P 500 + Dow Jones + Nasdaq 100
        sp = get_universe_tickers('sp500')
        dow = get_universe_tickers('dow30')
        nasdaq = get_universe_tickers('nasdaq100')
        combined = sorted(list(set(sp + dow + nasdaq)))
        if combined: return combined

    # Default to all available tickers in company_info or S&P 500
    if cur:
        try:
            cur.execute("SELECT ticker FROM company_info ORDER BY ticker")
            return [r['ticker'] for r in cur.fetchall()]
        except Exception:
            pass
    return get_universe_tickers('sp500')

def get_company_metadata() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    if not conn:
        return {}
    try:
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
    except Exception:
        return {}


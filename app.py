from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
import config
from core.db import get_connection, get_universe_tickers, get_company_metadata
from core.portfolio import simulate_portfolio
from core.charting import generate_candlestick_chart
from core.live_screener import scan_live_market, get_market_status
from strategies import list_strategies, get_strategy

app = FastAPI(
    title="Quantitative Strategy Backtesting Platform",
    description="Multi-strategy backtesting platform with portfolio sizing, risk filtering, and trade inspections.",
    version="2.0.0"
)

# Static and Templates
static_dir = os.path.join(config.BASE_DIR, "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
if os.path.exists(config.CHARTS_DIR):
    app.mount("/static/charts", StaticFiles(directory=config.CHARTS_DIR), name="charts")

templates = Jinja2Templates(directory=os.path.join(config.BASE_DIR, "templates"))

@app.on_event("startup")
def download_gcs_database_if_configured():
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    if bucket_name and not os.path.exists(config.DB_PATH_15M):
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob("screener_core_15min.db")
            if blob.exists():
                print(f"Downloading screener_core_15min.db from gs://{bucket_name}...")
                blob.download_to_filename(config.DB_PATH_15M)
                config.ACTIVE_DB = config.DB_PATH_15M
                print(f"Downloaded screener_core_15min.db ({os.path.getsize(config.DB_PATH_15M)} bytes)")
            else:
                blob_5m = bucket.blob("screener.db")
                if blob_5m.exists():
                    print(f"Downloading screener.db from gs://{bucket_name}...")
                    blob_5m.download_to_filename(config.DB_PATH_5M)
                    config.ACTIVE_DB = config.DB_PATH_5M
                    print(f"Downloaded screener.db ({os.path.getsize(config.DB_PATH_5M)} bytes)")
        except ImportError:
            print("Notice: google-cloud-storage not installed, skipping GCS download.")
        except Exception as e:
            print(f"Warning: Could not download DB from GCS bucket '{bucket_name}': {e}")

class BacktestRequest(BaseModel):
    strategy_id: str = "silent_flip"
    universe: str = "curated_30"  # 'curated_30', 'dow30', 'sp500', 'nasdaq100', 'custom'
    custom_tickers: Optional[str] = None
    time_range: str = "6m"       # '6m', '3m', '1m', 'custom'
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    max_trades_per_day: int = 4
    daily_capital: float = 50000.0
    account_size: Optional[float] = None
    max_monitor_stocks: int = 30
    direction_filter: str = "SHORTS_ONLY"
    min_planned_rr: float = 1.5
    strategy_params: Optional[Dict[str, Any]] = None

class LiveScanRequest(BaseModel):
    universe: str = "curated_30"
    custom_tickers: Optional[str] = None
    direction_filter: str = "SHORTS_ONLY"
    min_planned_rr: float = 1.5

@app.get("/api/market_status")
def api_get_market_status():
    return get_market_status()

@app.post("/api/scan_live")
def api_scan_live(req: LiveScanRequest):
    tickers = get_universe_tickers(req.universe, req.custom_tickers)
    if not tickers:
        raise HTTPException(status_code=400, detail="No tickers resolved for selected universe")
    # Cap to top 60 to prevent Yahoo Finance rate limits
    if len(tickers) > 60:
        tickers = tickers[:60]
    return scan_live_market(
        tickers=tickers,
        direction_filter=req.direction_filter,
        min_planned_rr=req.min_planned_rr
    )

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    index_file = os.path.join(config.BASE_DIR, "templates", "index.html")
    return FileResponse(index_file, media_type="text/html")

@app.get("/api/strategies")
def api_list_strategies():
    return list_strategies()

@app.get("/api/strategies/{strategy_id}")
def api_get_strategy(strategy_id: str):
    strat = get_strategy(strategy_id)
    if not strat:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {
        "id": strat.id,
        "name": strat.name,
        "category": strat.category,
        "description": strat.description,
        "parameters": [p.dict() for p in strat.default_parameters],
        "education": strat.get_educational_content()
    }

@app.get("/api/universes")
def api_get_universes():
    return [
        {
            "id": "curated_30",
            "name": "🎯 Curated 30 High-Edge Stocks (Recommended)",
            "description": "Top liquid defensive & financial components yielding 71% win rate on resistance fades",
            "count": len(config.HIGH_EDGE_30)
        },
        {
            "id": "dow30",
            "name": "🏛️ Dow Jones Industrial Average (30 Stocks)",
            "description": "30 institutional blue-chip components",
            "count": 30
        },
        {
            "id": "nasdaq100",
            "name": "💻 Nasdaq 100 (98 Stocks in DB)",
            "description": "Top non-financial growth & tech stocks",
            "count": 98
        },
        {
            "id": "sp500",
            "name": "🌐 S&P 500 Universe (502 Stocks)",
            "description": "Broad US large-cap equity market",
            "count": 502
        },
        {
            "id": "custom",
            "name": "✏️ Custom Watchlist",
            "description": "User-specified comma-separated ticker list",
            "count": 0
        }
    ]

@app.post("/api/backtest")
def api_run_backtest(req: BacktestRequest):
    strat = get_strategy(req.strategy_id)
    if not strat:
        raise HTTPException(status_code=404, detail=f"Strategy '{req.strategy_id}' not found")

    # 1. Resolve Universe Tickers
    tickers = get_universe_tickers(req.universe, req.custom_tickers)
    if not tickers:
        raise HTTPException(status_code=400, detail="No valid tickers selected for backtest")

    # 2. Date Filtering
    date_cutoff = None
    if req.time_range == "5y":
        date_cutoff = 20211011
    elif req.time_range == "2y":
        date_cutoff = 20241011
    elif req.time_range == "1y":
        date_cutoff = 20251011
    elif req.time_range == "6m":
        date_cutoff = 20260416
    elif req.time_range == "3m":
        date_cutoff = 20260706
    elif req.time_range == "1m":
        date_cutoff = 20260906

    params = req.strategy_params or {}
    params['direction_filter'] = req.direction_filter
    params['min_planned_rr'] = req.min_planned_rr
    if req.time_range == "custom":
        if req.start_date:
            params['start_date'] = req.start_date
        if req.end_date:
            params['end_date'] = req.end_date
    elif date_cutoff:
        params['start_date'] = date_cutoff

    # 3. Run Strategy Raw Backtest
    conn = get_connection()
    raw_trades = strat.run_backtest(conn, tickers, params)
    period_trading_days = None
    if hasattr(strat, 'get_trading_days'):
        period_trading_days = strat.get_trading_days(params.get('start_date'), params.get('end_date'))

    # 4. Apply Portfolio Constraints & Sizing
    meta_map = get_company_metadata()
    acc_size = req.account_size if req.account_size is not None else req.daily_capital
    results = simulate_portfolio(
        raw_trades=raw_trades,
        max_trades_per_day=req.max_trades_per_day,
        daily_capital=req.daily_capital,
        account_size=acc_size,
        max_monitor_stocks=req.max_monitor_stocks,
        metadata_map=meta_map,
        period_trading_days=period_trading_days
    )

    is_capped = req.time_range in ["1y", "2y", "5y"]
    cap_message = (
        "Database Notice: 15-minute intraday bars span 120 trading days (April 16 – October 6, 2026). "
        "Selecting 1Y, 2Y, or 5Y runs across all 120 available days."
        if is_capped else None
    )

    return {
        "strategy": {
            "id": strat.id,
            "name": strat.name
        },
        "universe": {
            "id": req.universe,
            "tickers_count": len(tickers)
        },
        "time_range": req.time_range,
        "is_date_capped": is_capped,
        "cap_message": cap_message,
        "results": results
    }

@app.get("/api/chart/{ticker}/{date}")
def api_get_chart(
    ticker: str, 
    date: int, 
    level_price: float = 0.0,
    entry_price: float = 0.0,
    stop_loss: float = 0.0,
    target_profit: float = 0.0,
    exit_price: float = 0.0,
    direction: str = "SHORT",
    tested_level: str = "RH",
    r_multiple: float = 0.0,
    dollar_pnl: float = 0.0,
    exit_reason: str = "MARKET_CLOSE"
):
    chart_name = f"chart_{ticker}_{date}.png"
    chart_path = os.path.join(config.CHARTS_DIR, chart_name)
    
    trade_info = {
        'level_price': level_price,
        'entry_price': entry_price,
        'stop_loss': stop_loss,
        'target_profit': target_profit,
        'exit_price': exit_price,
        'direction': direction,
        'tested_level': tested_level,
        'r_multiple': r_multiple,
        'dollar_pnl': dollar_pnl,
        'exit_reason': exit_reason
    }

    # Generate if not exists
    if not os.path.exists(chart_path):
        ok = generate_candlestick_chart(ticker, date, trade_info, chart_path)
        if not ok:
            raise HTTPException(status_code=404, detail="Candlestick data not found for ticker/date")

    return FileResponse(chart_path, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", config.SERVER_PORT))
    uvicorn.run("app:app", host=config.SERVER_HOST, port=port, reload=False)

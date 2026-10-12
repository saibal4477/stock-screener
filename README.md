# Quantitative Stock Screener & Backtesting Platform

A complete institutional-grade suite featuring:
1. **Interactive Web Backtesting Studio** (`app.py`) — Modern dark-mode web application for simulating realistic intraday day trading strategies with portfolio capital constraints, performance attribution breakdowns, and candlestick chart inspections.
2. **Unified MCP Server** (`server.py`) — Model Context Protocol (FastMCP) server that provides an indexed SQLite database-driven analytical screening and charting framework for AI assistants (Claude Desktop, Cursor, Gemini).

---

## 🚀 1. Interactive Web Backtesting Studio

Run backtests for institutional intraday strategies (starting with the **Silent Flip Strategy** — opening range resistance/support fade) with sub-second simulation latency.

### Key Features
* **Modular Strategy Architecture:** Strategy plugins in `strategies/` inherit from `BaseStrategy` and automatically register in the UI.
* **Realistic Portfolio Simulation:** Configure **Max Daily Capital Deployed** (e.g. $50,000), **Max Positions/Day** (e.g. 4 trades = $12,500/position), watchlist ranking, and intra-day cash sizing. Zero overnight risk (all trades exit by 3:55 PM EST).
* **Performance Attribution & Win Breakdown:**
  * **Short vs Long** (Discovers directional asymmetries, e.g., +127R shorting resistance vs negative edge buying opening dips).
  * **Sector Analysis** (Defensives, Utilities, Financials, REITs vs high-beta Tech).
  * **Key Structural Levels** (Range High $RH$, Flip High $FH$, Range Low $RL$, Flip Low $FL$).
  * **Reward/Risk & Beta Sensitivity** (Filters for $R:R \ge 1.5$ yielding >70% win rates).
* **Executed Trades Explorer:** Column-sortable interactive table with win/loss badges, dollar P&L, R-multiples, and exit reasons.
* **Dynamic 15-Minute Candlestick Chart Modal:** Click any trade row to generate a high-resolution 15-minute candlestick chart with entry, stop loss, profit target, and exit execution overlays.

### Quickstart (Web App)
1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Launch the Server:**
   ```bash
   python app.py
   ```
3. **Open in Browser:**
   Navigate to [http://localhost:8000](http://localhost:8000)

---

## 🛠️ 2. Architecture & MCP Server Replication Guide

This project is completely decoupled from local text files. The database is populated, indexed, and updated entirely via MCP tools. The system can be fully recreated on a clean computer using just **three files** and your Stooq ZIP archive:

1. **`server.py`** — The unified MCP server application.
2. **`trading_rules.json`** — Quantitative sizing and risk configurations.
3. **`strategies.json`** — Custom Strategy DSL screen queries.

No text files or raw directories need to be copied! Follow the step-by-step guide below to recreate the database.

### Step-by-Step Database Replication
Follow these steps to instantiate and populate the database on a clean machine:

1. **Install Dependencies**:
   ```bash
   pip install pandas numpy yfinance fastmcp matplotlib requests
   ```
2. **Download Historical Prices ZIP**:
   Download the historical prices dataset from [Stooq](https://stooq.com) (e.g. `d_us_txt.zip`).
3. **Import Historical Data**:
   Call `import_historical_data_from_zip(zip_path)` pointing to the ZIP file. This unzips files into a transient folder, bulk-inserts all records into the `historical_prices` database table, recalculates indicators, and deletes the temporary files.
   * *Terminal Command*:
     ```bash
     python -c "import server; print(server.import_historical_data_from_zip(r'C:\path\to\d_us_txt.zip'))"
     ```
4. **Populate Company Fundamentals & Betas**:
   Call `download_and_store_fundamentals()`. This downloads company metadata (sector, industry, market cap) from the Nasdaq API and computes historical stock Betas relative to `SPY` in parallel.
   * *Terminal Command*:
     ```bash
     python -c "import server; print(server.download_and_store_fundamentals())"
     ```
5. **Populate ETF Holdings**:
   Call `bootstrap_etf_holdings()`. This downloads the top holdings of all active ETFs in the database using Yahoo Finance.
   * *Terminal Command*:
     ```bash
     python -c "import server; print(server.bootstrap_etf_holdings())"
     ```

---

## 🔌 Exposed MCP Tools Reference

Here is a list of the tools exposed by the server, organized by function:

### 1. Database Operations & Ingestion
* **`import_historical_data_from_zip(zip_path: str)`**:
  Extracts and loads historical price records from a Stooq ZIP file, recalculates indicator caches, and cleans up temporary extraction files.
* **`update_database_from_file(file_path: str)`**:
  Reads a single daily update text file (e.g. `20260701_d.txt`) and inserts records directly into the database.
* **`download_and_store_fundamentals()`**:
  Populates company metadata from the Nasdaq API and calculates parallel Betas relative to `SPY`.
* **`bootstrap_etf_holdings()`**:
  Bootstraps ETF component weightings into the database using Yahoo Finance.
* **`reindex_database()`**:
  Triggers database indicator calculations for all tickers in the database.

### 2. Screening & Querying
* **`get_available_fields()`**:
  Lists all columns, types, and descriptions available in the database schema.
* **`query_screener_db(sql: str)`**:
  Executes read-only SQL `SELECT` queries for completely custom screens.
* **`screen_stocks(exchange, type, min_price, max_price, rsi_state, price_vs_sma, sort_by)`**:
  A structured interface for standard stock/ETF queries.
* **`screen_trades(strategy_filter: str)`**:
  Screens tickers using Strategy DSL queries defined in `strategies.json`.
* **`screen_pullback_trades()`**:
  Scans active stocks for custom long pullback opportunities.
* **`screen_short_pullback_trades()`**:
  Scans active stocks for custom short pullback opportunities.
* **`screen_crossover_trades()`**:
  Scans active stocks for 50/200 SMA crossovers.

### 3. Charting & Analysis
* **`get_stock_history(ticker: str, limit: int)`**:
  Returns raw historical daily price and volume data for a stock.
* **`get_stock_chart(ticker, days, support_prices, resistance_prices, stop_losses, target_prices)`**:
  Generates a high-quality, dark-themed candlestick chart with 50/200 SMAs and volume subplots.
* **`find_support_resistance(ticker: str, days: int)`**:
  Clusters and returns raw Swing High/Low levels.
* **`get_support_resistance_levels(ticker: str)`**:
  Detects S/R levels across timeframes and generates an overlay chart.
* **`get_sector_rotation(days: int)`**:
  Plots a sector rotation performance ranking heatmap.

### 4. Trade Planning & ETF Lookups
* **`generate_trade_plan(ticker: str)`**:
  Generates a comprehensive trade plan following 10 quantitative risk/reward rules.
* **`get_etf_holdings(etf_ticker: str)`**:
  Lists the top underlying stocks of an ETF.
* **`find_etfs_holding_stock(stock_ticker: str)`**:
  Finds all ETFs holding a specific stock.

---

## ⚙️ MCP Client Configuration

### Claude Desktop (Windows)
Add this to your `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "stock-screener": {
      "command": "python",
      "args": [
        "C:\\Users\\saiba\\.gemini\\antigravity\\scratch\\mcp-stock-screener\\server.py"
      ],
      "cwd": "C:\\Users\\saiba\\.gemini\\antigravity\\scratch\\mcp-stock-screener"
    }
  }
}
```

### Cursor IDE
1. Go to **Cursor Settings** > **Features** > **MCP**.
2. Click **+ Add New MCP Server**.
3. Set as:
   * **Name**: `stock-screener`
   * **Type**: `command`
   * **Command**: `python C:\Users\saiba\.gemini\antigravity\scratch\mcp-stock-screener\server.py`

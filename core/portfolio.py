import os
import pandas as pd
import numpy as np
from typing import List, Dict, Any
import config

def simulate_portfolio(
    raw_trades: List[Dict[str, Any]],
    max_trades_per_day: int = 4,
    daily_capital: float = 50000.0,
    account_size: float = 100000.0,
    max_monitor_stocks: int = 30,
    metadata_map: Dict[str, Dict[str, Any]] = None,
    period_trading_days: List[int] = None
) -> Dict[str, Any]:
    if not raw_trades:
        return {
            "summary": {
                "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "net_profit": 0.0, "total_r": 0.0, "profit_factor": 0.0,
                "max_drawdown": 0.0, "max_dd_pct": 0.0,
                "return_on_deployed_pct": 0.0, "return_on_account_pct": 0.0,
                "position_size": daily_capital / max_trades_per_day if max_trades_per_day > 0 else 0,
                "total_trading_days": len(period_trading_days) if period_trading_days else 0,
                "active_trading_days": 0
            },
            "equity_curve": [],
            "daily_distribution": {str(i): 0 for i in range(max_trades_per_day + 1)},
            "monthly_performance": [],
            "trades": []
        }

    df = pd.DataFrame(raw_trades)
    
    # 1. Constrain monitored stocks if universe exceeds max_monitor_stocks
    unique_tickers = df['ticker'].unique()
    if len(unique_tickers) > max_monitor_stocks:
        # Prioritize monitored stocks by proven statistical edge (Net R)
        ticker_counts = df.groupby('ticker')['r_multiple'].sum().sort_values(ascending=False)
        allowed_tickers = set(ticker_counts.head(max_monitor_stocks).index)
        df = df[df['ticker'].isin(allowed_tickers)].copy()

    # 2. Position Sizing
    pos_size = daily_capital / max_trades_per_day

    # 3. Daily Execution Cap: Prioritize highest planned R:R setups on busy days
    selected_list = []
    unique_dates = df['date'].unique()
    daily_counts = {}

    for d in unique_dates:
        day_trades = df[df['date'] == d].sort_values('planned_rr', ascending=False)
        chosen = day_trades.head(max_trades_per_day)
        selected_list.append(chosen)
        cnt = len(chosen)
        daily_counts[cnt] = daily_counts.get(cnt, 0) + 1

    df_sim = pd.concat(selected_list, ignore_index=True) if selected_list else pd.DataFrame()
    if df_sim.empty:
        return {}

    # Sort chronologically
    df_sim['date_str'] = pd.to_datetime(df_sim['date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    df_sim['month'] = pd.to_datetime(df_sim['date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m')
    df_sim = df_sim.sort_values(['date', 'entry_time', 'ticker']).reset_index(drop=True)

    # Calculate Cash Sizing & Dollar PnL
    df_sim['shares'] = np.floor(pos_size / df_sim['entry_price']).astype(int)
    
    # Dollar P&L
    is_short = df_sim['direction'] == 'SHORT'
    df_sim['dollar_pnl'] = np.where(
        is_short,
        (df_sim['entry_price'] - df_sim['exit_price']) * df_sim['shares'],
        (df_sim['exit_price'] - df_sim['entry_price']) * df_sim['shares']
    ).round(2)

    df_sim['dollar_risk'] = (df_sim['initial_risk'] * df_sim['shares']).round(2)
    df_sim['actual_pos_val'] = (df_sim['entry_price'] * df_sim['shares']).round(2)

    # Equity Curve
    df_sim['cum_pnl'] = df_sim['dollar_pnl'].cumsum().round(2)
    df_sim['equity'] = (account_size + df_sim['cum_pnl']).round(2)
    df_sim['cum_r'] = df_sim['r_multiple'].cumsum().round(2)

    peak = df_sim['equity'].cummax()
    dd = peak - df_sim['equity']
    max_dd = round(float(dd.max()), 2)
    max_dd_pct = round((max_dd / daily_capital) * 100, 2)

    # Performance Stats
    total_trades = len(df_sim)
    wins = df_sim[df_sim['dollar_pnl'] > 0]
    losses = df_sim[df_sim['dollar_pnl'] < 0]
    bes = df_sim[df_sim['dollar_pnl'] == 0]

    net_profit = round(float(df_sim['dollar_pnl'].sum()), 2)
    gross_w = round(float(wins['dollar_pnl'].sum()), 2)
    gross_l = round(float(abs(losses['dollar_pnl'].sum())), 2)
    pf = round(gross_w / gross_l, 2) if gross_l > 0 else (999.0 if gross_w > 0 else 0.0)
    win_rate = round(len(wins) / total_trades * 100, 1) if total_trades > 0 else 0.0

    ret_deployed = round((net_profit / daily_capital) * 100, 2)
    ret_account = round((net_profit / account_size) * 100, 2)

    # Monthly performance
    m_group = df_sim.groupby('month').agg(
        trades=('ticker', 'count'),
        wins=('dollar_pnl', lambda x: int((x > 0).sum())),
        losses=('dollar_pnl', lambda x: int((x < 0).sum())),
        net_pnl=('dollar_pnl', 'sum'),
        total_r=('r_multiple', 'sum')
    ).reset_index()
    m_group['win_rate'] = (m_group['wins'] / m_group['trades'] * 100).round(1)
    m_group['net_pnl'] = m_group['net_pnl'].round(2)
    m_group['total_r'] = m_group['total_r'].round(2)
    monthly_performance = m_group.to_dict(orient='records')

    # Daily distribution (0 to max_trades_per_day)
    total_trading_days = len(period_trading_days) if period_trading_days is not None else len(unique_dates)
    dist = {}
    active_days_count = len(unique_dates)
    dist['0'] = max(0, total_trading_days - active_days_count)
    for i in range(1, max_trades_per_day + 1):
        dist[str(i)] = daily_counts.get(i, 0)

    # Equity curve series for Chart.js
    equity_series = []
    for idx, row in df_sim.iterrows():
        equity_series.append({
            "idx": idx + 1,
            "date": row['date_str'],
            "ticker": row['ticker'],
            "dollar_pnl": row['dollar_pnl'],
            "cum_pnl": row['cum_pnl'],
            "equity": row['cum_pnl'],
            "cum_r": row['cum_r']
        })

    # Enrich trades with sector / company name / metadata
    meta = metadata_map or {}
    df_sim['sector'] = df_sim['ticker'].map(lambda x: meta.get(x, {}).get('sector', 'Other'))
    df_sim['company_name'] = df_sim['ticker'].map(lambda x: meta.get(x, {}).get('name', x))
    df_sim['beta'] = df_sim['ticker'].map(lambda x: meta.get(x, {}).get('beta', 1.0))
    df_sim['market_cap'] = df_sim['ticker'].map(lambda x: meta.get(x, {}).get('market_cap', 0))

    # Helper for grouped breakdown metrics
    def agg_metrics(group_by_cols, sort_by='net_pnl', ascending=False):
        if not all(col in df_sim.columns for col in (group_by_cols if isinstance(group_by_cols, list) else [group_by_cols])):
            return []
        
        grouped = df_sim.groupby(group_by_cols, observed=False).agg(
            trades=('ticker', 'count'),
            wins=('dollar_pnl', lambda x: int((x > 0).sum())),
            losses=('dollar_pnl', lambda x: int((x < 0).sum())),
            breakevens=('dollar_pnl', lambda x: int((x == 0).sum())),
            net_pnl=('dollar_pnl', 'sum'),
            total_r=('r_multiple', 'sum'),
            gross_win=('dollar_pnl', lambda x: float(x[x > 0].sum())),
            gross_loss=('dollar_pnl', lambda x: float(abs(x[x < 0].sum())))
        ).reset_index()

        grouped['win_rate'] = np.where(grouped['trades'] > 0, (grouped['wins'] / grouped['trades'] * 100).round(1), 0.0)
        grouped['net_pnl'] = grouped['net_pnl'].round(2)
        grouped['total_r'] = grouped['total_r'].round(2)
        grouped['profit_factor'] = np.where(
            grouped['gross_loss'] > 0,
            (grouped['gross_win'] / grouped['gross_loss']).round(2),
            np.where(grouped['gross_win'] > 0, 999.0, 0.0)
        )
        if sort_by in grouped.columns:
            grouped = grouped.sort_values(sort_by, ascending=ascending)
        return grouped.to_dict(orient='records')

    # 1. Direction Breakdown (Short vs Long)
    direction_breakdown = agg_metrics('direction')

    # 2. Sector Breakdown
    sector_breakdown = agg_metrics('sector')

    # 3. Structural Level Breakdown (RH, FH, RL, FL)
    level_breakdown = agg_metrics(['tested_level', 'direction'], sort_by='total_r')

    # 4. Planned Reward/Risk Tier
    if 'planned_rr' in df_sim.columns:
        df_sim['rr_tier'] = pd.cut(
            df_sim['planned_rr'],
            bins=[-np.inf, 1.49, 1.99, 2.49, np.inf],
            labels=['< 1.5x (Low R:R)', '1.5x – 2.0x (Target)', '2.0x – 2.5x (High)', '≥ 2.5x (Extreme)']
        )
        rr_breakdown = agg_metrics('rr_tier', sort_by='rr_tier', ascending=True)
    else:
        rr_breakdown = []

    # 5. Exit Reason Breakdown
    exit_breakdown = agg_metrics('exit_reason', sort_by='trades')

    # 6. Beta Sensitivity Tier
    b_vals = pd.to_numeric(df_sim['beta'], errors='coerce').fillna(1.0)
    df_sim['beta_tier'] = pd.cut(
        b_vals,
        bins=[-np.inf, 0.85, 1.25, np.inf],
        labels=['Defensive (Beta < 0.85)', 'Market-Neutral (0.85 – 1.25)', 'High-Beta Momentum (> 1.25)']
    )
    beta_breakdown = agg_metrics('beta_tier', sort_by='beta_tier', ascending=True)

    trade_records = []
    for idx, row in df_sim.iterrows():
        t_dict = row.to_dict()
        t_dict['trade_id'] = idx + 1
        # Check if chart image exists
        chart_cand = f"trade_port_{idx+1:03d}_{row['ticker']}_{row['date']}.png"
        t_dict['chart_url'] = f"/static/charts/{chart_cand}" if os.path.exists(os.path.join(config.CHARTS_DIR, chart_cand)) else None
        trade_records.append(t_dict)

    return {
        "summary": {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(bes),
            "win_rate": win_rate,
            "net_profit": net_profit,
            "total_r": round(float(df_sim['r_multiple'].sum()), 2),
            "profit_factor": pf,
            "max_drawdown": max_dd,
            "max_dd_pct": max_dd_pct,
            "return_on_deployed_pct": ret_deployed,
            "return_on_account_pct": ret_account,
            "avg_win": round(float(wins['dollar_pnl'].mean()), 2) if not wins.empty else 0.0,
            "avg_loss": round(float(losses['dollar_pnl'].mean()), 2) if not losses.empty else 0.0,
            "avg_risk_per_trade": round(float(df_sim['dollar_risk'].mean()), 2) if not df_sim.empty else 0.0,
            "position_size": pos_size,
            "daily_capital": daily_capital,
            "account_size": account_size,
            "max_trades_per_day": max_trades_per_day,
            "total_trading_days": total_trading_days,
            "active_trading_days": active_days_count,
            "start_date": str(df_sim['date'].min()) if not df_sim.empty else None,
            "end_date": str(df_sim['date'].max()) if not df_sim.empty else None
        },
        "equity_curve": equity_series,
        "daily_distribution": dist,
        "monthly_performance": monthly_performance,
        "breakdowns": {
            "direction": direction_breakdown,
            "sector": sector_breakdown,
            "level": level_breakdown,
            "rr_tier": rr_breakdown,
            "exit_reason": exit_breakdown,
            "beta_tier": beta_breakdown
        },
        "trades": trade_records
    }

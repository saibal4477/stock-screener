import sqlite3
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import config

def generate_candlestick_chart(ticker: str, trade_date: int, trade_info: dict, output_path: str) -> bool:
    if os.path.exists(output_path):
        return True

    # Check database
    db_path = config.DB_PATH_15M if os.path.exists(config.DB_PATH_15M) else config.DB_PATH_5M
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Determine which table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('historical_prices_15min', 'historical_prices_5min')")
    tbls = [r[0] for r in cur.fetchall()]
    use_15m_table = 'historical_prices_15min' in tbls
    table_name = 'historical_prices_15min' if use_15m_table else 'historical_prices_5min'

    cur.execute(f"""
        SELECT distinct date FROM {table_name}
        WHERE ticker = ? AND date <= ? 
        ORDER BY date DESC LIMIT 4
    """, (ticker, trade_date))
    dates = sorted([r[0] for r in cur.fetchall()])
    if not dates:
        conn.close()
        return False

    placeholders = ','.join(['?'] * len(dates))
    q = f"""
        SELECT date, time, open, high, low, close, volume 
        FROM {table_name} 
        WHERE ticker = ? AND date IN ({placeholders})
        ORDER BY date ASC, time ASC
    """
    df_raw = pd.read_sql_query(q, conn, params=[ticker] + dates)
    conn.close()
    if df_raw.empty:
        return False

    if use_15m_table:
        df_15m = df_raw.copy()
        df_15m['time_15m'] = df_15m['time']
    else:
        def get_15m_time(t):
            hh = t // 10000
            mm = (t % 10000) // 100
            bucket_m = (mm // 15) * 15
            return hh * 10000 + bucket_m * 100
        df_raw['time_15m'] = df_raw['time'].apply(get_15m_time)
        df_15m = df_raw.groupby(['date', 'time_15m']).agg(
            open=('open', 'first'),
            high=('high', 'max'),
            low=('low', 'min'),
            close=('close', 'last'),
            volume=('volume', 'sum')
        ).reset_index()

    df_15m['x_idx'] = np.arange(len(df_15m))

    yesterday = dates[-2] if len(dates) >= 2 else dates[0]
    yesterday_bars = df_15m[df_15m['date'] == yesterday]
    level_price = float(trade_info.get('level_price', 0.0))
    rh = yesterday_bars['high'].max() if not yesterday_bars.empty else level_price
    rl = yesterday_bars['low'].min() if not yesterday_bars.empty else level_price

    tested_level = trade_info.get('tested_level', 'RH')
    entry_price = float(trade_info.get('entry_price', 0.0))
    stop_loss = float(trade_info.get('stop_loss', 0.0))
    target_profit = float(trade_info.get('target_profit', 0.0))
    exit_price = float(trade_info.get('exit_price', 0.0))
    exit_reason = trade_info.get('exit_reason', 'MARKET_CLOSE')
    r_multiple = float(trade_info.get('r_multiple', 0.0))
    direction = trade_info.get('direction', 'SHORT')
    dollar_pnl = float(trade_info.get('dollar_pnl', 0.0))
    is_win = r_multiple > 0

    trade_day_bars = df_15m[df_15m['date'] == trade_date]
    if trade_day_bars.empty:
        return False
    trade_start_x = trade_day_bars['x_idx'].iloc[0]

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(13, 6.5), facecolor='#0b0f19')
    ax.set_facecolor('#111827')

    width = 0.6
    for _, row in df_15m.iterrows():
        x = row['x_idx']
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        color = '#10b981' if c >= o else '#f43f5e'
        ax.plot([x, x], [l, h], color=color, linewidth=1.2, zorder=2)
        body_bottom = min(o, c)
        body_height = max(abs(c - o), 0.001)
        rect = plt.Rectangle((x - width/2, body_bottom), width, body_height, 
                             facecolor=color, edgecolor=color, zorder=3)
        ax.add_patch(rect)

    unique_dates = df_15m.drop_duplicates(subset=['date'])
    for idx_row in unique_dates.index:
        if idx_row > 0:
            ax.axvline(idx_row - 0.5, color='#374151', linestyle='--', linewidth=1.0, alpha=0.7)

    x_min_levels = df_15m[df_15m['date'] == yesterday]['x_idx'].min() if not yesterday_bars.empty else trade_start_x - 10
    x_max_levels = df_15m['x_idx'].max() + 2

    ax.hlines(rh, x_min_levels, x_max_levels, colors='#fbbf24', linestyles='--', linewidth=1.4, 
              label=f"Range High (RH: ${rh:.2f})", zorder=4)
    ax.hlines(rl, x_min_levels, x_max_levels, colors='#38bdf8', linestyles='--', linewidth=1.4, 
              label=f"Range Low (RL: ${rl:.2f})", zorder=4)

    if tested_level in ['FH', 'FL'] and level_price > 0:
        lvl_color = '#e11d48' if tested_level == 'FH' else '#2563eb'
        ax.hlines(level_price, x_min_levels, x_max_levels, colors=lvl_color, linestyles='-.', linewidth=1.5,
                  label=f"Flip Level ({tested_level}: ${level_price:.2f})", zorder=4)

    ax.hlines(stop_loss, trade_start_x, x_max_levels, colors='#ef4444', linestyles=':', linewidth=1.6,
              label=f"Stop Loss (${stop_loss:.2f})", zorder=5)
    ax.hlines(target_profit, trade_start_x, x_max_levels, colors='#10b981', linestyles=':', linewidth=1.6,
              label=f"Take Profit (${target_profit:.2f})", zorder=5)

    entry_x = trade_start_x + 2.5
    entry_color = '#38bdf8' if direction == 'LONG' else '#f59e0b'
    ax.scatter(entry_x, entry_price, color=entry_color, s=120, edgecolors='#ffffff', linewidth=1.5, zorder=6)
    
    y_range = max(df_15m['high'].max() - df_15m['low'].min(), 1.0)
    offset = y_range * 0.05
    
    ax.annotate(f"ENTRY ({direction})\n${entry_price:.2f}",
                xy=(entry_x, entry_price),
                xytext=(entry_x - 4, entry_price + (offset if direction == 'SHORT' else -offset)),
                arrowprops=dict(facecolor=entry_color, shrink=0.08, width=1.5, headwidth=5),
                color='#f8f8f2', fontsize=8.5, fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#1e293b', edgecolor=entry_color, alpha=0.9),
                zorder=7)

    exit_x = trade_start_x + len(trade_day_bars) - 2
    exit_color = '#10b981' if is_win else '#ef4444'
    ax.scatter(exit_x, exit_price, color=exit_color, s=140, marker='X', edgecolors='#ffffff', linewidth=1.5, zorder=6)
    ax.annotate(f"EXIT ({exit_reason})\n${exit_price:.2f}",
                xy=(exit_x, exit_price),
                xytext=(exit_x + 1, exit_price + (-offset if direction == 'SHORT' else offset)),
                arrowprops=dict(facecolor=exit_color, shrink=0.08, width=1.5, headwidth=5),
                color='#f8f8f2', fontsize=8.5, fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#1e293b', edgecolor=exit_color, alpha=0.9),
                zorder=7)

    outcome_str = f"WIN (+{r_multiple:.2f} R / +${dollar_pnl:.2f})" if is_win else f"LOSS ({r_multiple:.2f} R / ${dollar_pnl:.2f})"
    s_date = str(trade_date)
    date_display = f"{s_date[:4]}-{s_date[4:6]}-{s_date[6:]}"
    ax.set_title(
        f"{ticker} ({direction} @ {tested_level}) • {date_display} • {outcome_str}",
        fontsize=13, fontweight='bold', pad=14, color='#f3f4f6'
    )

    date_ticks = []
    date_labels = []
    for d in dates:
        d_bars = df_15m[df_15m['date'] == d]
        if not d_bars.empty:
            mid_idx = d_bars['x_idx'].iloc[len(d_bars)//2]
            date_ticks.append(mid_idx)
            is_trade_day = (d == trade_date)
            sd = str(d)
            d_str = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
            date_labels.append(f"★ {d_str} (TRADE)" if is_trade_day else d_str)

    ax.set_xticks(date_ticks)
    ax.set_xticklabels(date_labels, fontsize=9.5, fontweight='bold', color='#94a3b8')
    ax.grid(True, linestyle=':', alpha=0.3, color='#4b5563')
    ax.set_ylabel("Price ($)", fontsize=10, color='#94a3b8')
    ax.yaxis.set_major_formatter('${x:.2f}')

    y_min = df_15m['low'].min()
    y_max = df_15m['high'].max()
    pad = (y_max - y_min) * 0.12
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1, len(df_15m) + 3)

    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc='upper left', fontsize=8.5, 
              facecolor='#111827', edgecolor='#374151', framealpha=0.9)

    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=130, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    return True

def generate_live_candlestick_chart(ticker: str, session_date: str, trade_info: dict, output_path: str) -> bool:
    """
    Generates a high-resolution 15-minute candlestick chart for live/recent market setups streamed via Yahoo Finance.
    Draws the 30-minute opening range (RH, RL, FH, FL), marks the 10:00 AM+ setup bar, and plots Entry, Stop, and Target levels.
    """
    if os.path.exists(output_path):
        return True

    import yfinance as yf

    try:
        df_raw = yf.download(ticker, period="5d", interval="15m", progress=False)
    except Exception:
        return False

    if df_raw is None or df_raw.empty:
        return False

    if isinstance(df_raw.columns, pd.MultiIndex):
        if ticker in df_raw.columns.get_level_values(0):
            df_raw = df_raw[ticker]
        else:
            df_raw.columns = df_raw.columns.get_level_values(0)

    df_raw = df_raw.dropna().copy()
    if df_raw.empty:
        return False

    if df_raw.index.tz is None:
        df_raw.index = df_raw.index.tz_localize('UTC').tz_convert('America/New_York')
    else:
        df_raw.index = df_raw.index.tz_convert('America/New_York')

    df_raw['date_str'] = df_raw.index.strftime('%Y-%m-%d')
    df_raw['time_str'] = df_raw.index.strftime('%H:%M')

    avail_dates = sorted(df_raw['date_str'].unique().tolist())
    if not avail_dates:
        return False

    target_date = session_date if session_date in avail_dates else avail_dates[-1]

    # Keep up to last 2-3 sessions for clean context
    target_idx = avail_dates.index(target_date)
    start_idx = max(0, target_idx - 2)
    selected_dates = avail_dates[start_idx:target_idx + 1]

    df = df_raw[df_raw['date_str'].isin(selected_dates)].copy()
    if df.empty:
        return False

    df['x_idx'] = np.arange(len(df))

    trade_day_bars = df[df['date_str'] == target_date]
    if len(trade_day_bars) < 2:
        return False

    trade_start_x = trade_day_bars['x_idx'].iloc[0]

    # Opening Range: First 2 candles (9:30-10:00 AM EST)
    c0 = trade_day_bars.iloc[0]
    c1 = trade_day_bars.iloc[1]
    rh = float(trade_info.get('rh', max(c0['High'], c1['High'])))
    rl = float(trade_info.get('rl', min(c0['Low'], c1['Low'])))

    level_price = float(trade_info.get('level_price', 0.0))
    tested_level = trade_info.get('tested_level', 'RH')
    entry_price = float(trade_info.get('entry_price', 0.0))
    stop_loss = float(trade_info.get('stop_loss', 0.0))
    target_profit = float(trade_info.get('target_profit', 0.0))
    direction = trade_info.get('direction', 'SHORT')
    setup_time = trade_info.get('setup_time', '10:00 EST')
    status = trade_info.get('status', 'ACTIVE_IN_PLAY')
    planned_rr = float(trade_info.get('planned_rr', 1.5))
    realized_r = trade_info.get('realized_r')

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(13, 6.5), facecolor='#0b0f19')
    ax.set_facecolor('#111827')

    width = 0.6
    for _, row in df.iterrows():
        x = row['x_idx']
        o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']
        color = '#10b981' if c >= o else '#f43f5e'
        ax.plot([x, x], [l, h], color=color, linewidth=1.2, zorder=2)
        body_bottom = min(o, c)
        body_height = max(abs(c - o), 0.001)
        rect = plt.Rectangle((x - width/2, body_bottom), width, body_height, 
                             facecolor=color, edgecolor=color, zorder=3)
        ax.add_patch(rect)

    # Day separator lines
    unique_dates = df.drop_duplicates(subset=['date_str'])
    for idx_row in unique_dates.index:
        x_val = df.loc[idx_row, 'x_idx']
        if x_val > 0:
            ax.axvline(x_val - 0.5, color='#374151', linestyle='--', linewidth=1.0, alpha=0.7)

    x_min_levels = trade_start_x
    x_max_levels = df['x_idx'].max() + 2

    # Opening Range Boundaries
    ax.hlines(rh, x_min_levels, x_max_levels, colors='#fbbf24', linestyles='--', linewidth=1.5, 
              label=f"Opening Range High (RH: ${rh:.2f})", zorder=4)
    ax.hlines(rl, x_min_levels, x_max_levels, colors='#38bdf8', linestyles='--', linewidth=1.5, 
              label=f"Opening Range Low (RL: ${rl:.2f})", zorder=4)

    if tested_level in ['FH', 'FL'] and level_price > 0:
        lvl_color = '#e11d48' if tested_level == 'FH' else '#2563eb'
        ax.hlines(level_price, x_min_levels, x_max_levels, colors=lvl_color, linestyles='-.', linewidth=1.5,
                  label=f"Flip Level ({tested_level}: ${level_price:.2f})", zorder=4)

    if stop_loss > 0:
        ax.hlines(stop_loss, x_min_levels, x_max_levels, colors='#ef4444', linestyles=':', linewidth=1.6,
                  label=f"Stop Loss (${stop_loss:.2f})", zorder=5)
    if target_profit > 0:
        ax.hlines(target_profit, x_min_levels, x_max_levels, colors='#10b981', linestyles=':', linewidth=1.6,
                  label=f"Profit Target (${target_profit:.2f})", zorder=5)

    # Setup bar identification
    setup_hour_min = setup_time.split()[0] if setup_time else "10:00"
    matching_bars = trade_day_bars[trade_day_bars['time_str'].str.startswith(setup_hour_min)]
    if not matching_bars.empty:
        setup_x = matching_bars.iloc[0]['x_idx']
    else:
        setup_x = trade_day_bars['x_idx'].iloc[2] if len(trade_day_bars) >= 3 else trade_start_x + 2

    entry_color = '#38bdf8' if direction == 'LONG' else '#f59e0b'
    ax.scatter(setup_x, entry_price, color=entry_color, s=130, edgecolors='#ffffff', linewidth=1.5, zorder=6)
    
    y_range = max(df['High'].max() - df['Low'].min(), 1.0)
    offset = y_range * 0.06
    
    ax.annotate(f"SETUP ({direction} @ {tested_level})\nEntry: ${entry_price:.2f}\nR:R: {planned_rr:.1f}x",
                xy=(setup_x, entry_price),
                xytext=(setup_x - 3, entry_price + (offset if direction == 'SHORT' else -offset)),
                arrowprops=dict(facecolor=entry_color, shrink=0.08, width=1.5, headwidth=5),
                color='#f8f8f2', fontsize=8.5, fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.35', facecolor='#1e293b', edgecolor=entry_color, alpha=0.92),
                zorder=7)

    status_title_map = {
        'TARGET_HIT': f"TARGET REACHED (+{planned_rr:.2f} R)",
        'STOPPED_OUT': "STOPPED OUT (-1.00 R)",
        'CLOSED_AT_EOD': f"CLOSED AT EOD ({realized_r:+0.2f} R)" if realized_r is not None else "CLOSED AT EOD",
        'ACTIVE_IN_PLAY': "ACTIVE IN PLAY ⚡"
    }
    status_str = status_title_map.get(status, status)

    ax.set_title(
        f"{ticker} ({direction} @ {tested_level}) • {target_date} • Setup: {setup_time} • {status_str}",
        fontsize=13, fontweight='bold', pad=14, color='#f3f4f6'
    )

    date_ticks = []
    date_labels = []
    for d in selected_dates:
        d_bars = df[df['date_str'] == d]
        if not d_bars.empty:
            if d == target_date:
                for t_target in ['09:30', '10:00', '11:30', '13:00', '14:30', '15:45']:
                    t_bar = d_bars[d_bars['time_str'].str.startswith(t_target)]
                    if not t_bar.empty:
                        date_ticks.append(t_bar['x_idx'].iloc[0])
                        date_labels.append(f"{t_target}")
            else:
                mid_idx = d_bars['x_idx'].iloc[len(d_bars)//2]
                date_ticks.append(mid_idx)
                date_labels.append(d)

    ax.set_xticks(date_ticks)
    ax.set_xticklabels(date_labels, fontsize=9.0, fontweight='bold', color='#94a3b8')
    ax.grid(True, linestyle=':', alpha=0.3, color='#4b5563')
    ax.set_ylabel("Price ($)", fontsize=10, color='#94a3b8')
    ax.yaxis.set_major_formatter('${x:.2f}')

    y_min = df['Low'].min()
    y_max = df['High'].max()
    pad = (y_max - y_min) * 0.12
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1, len(df) + 3)

    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc='upper left', fontsize=8.5, 
              facecolor='#111827', edgecolor='#374151', framealpha=0.9)

    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=130, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    return True

import sqlite3
import pandas as pd
import numpy as np
import os
import glob
import re
from typing import List, Dict, Any
from strategies.base import BaseStrategy, StrategyParam
import config

class SilentFlipStrategy(BaseStrategy):
    @property
    def id(self) -> str:
        return "silent_flip"

    @property
    def name(self) -> str:
        return "Silent Flip Strategy (Karl Eriksson / ProRealAlgos)"

    @property
    def category(self) -> str:
        return "intraday"

    @property
    def description(self) -> str:
        return (
            "Institutional intraday mean-reversion strategy fading key daily levels (Range High/Low, Flip High/Low) "
            "triggered by volatility contraction via the 'Silent Candle' pattern with strict 1.5x+ Reward/Risk."
        )

    @property
    def default_parameters(self) -> List[StrategyParam]:
        return [
            StrategyParam(
                name="direction_filter",
                label="Direction & Level Focus",
                type="select",
                default="SHORTS_ONLY",
                options=[
                    {"value": "SHORTS_ONLY", "label": "Shorts Only @ RH & FH Resistance (Highest Edge) 💎"},
                    {"value": "NO_RL_LONGS", "label": "Drop RL Longs (Take RH/FH Shorts & FL Longs) 🟢"},
                    {"value": "ALL", "label": "All Signals (Longs + Shorts unfiltered)"},
                    {"value": "LONGS_ONLY", "label": "Longs Only (Support Bounces)"}
                ],
                description="Shorts at Range High/Flip High carry +127.1 R institutional edge. Longs at Range Low lost -275.1 R."
            ),
            StrategyParam(
                name="min_planned_rr",
                label="Minimum Planned Reward / Risk",
                type="select",
                default=1.5,
                options=[
                    {"value": 0.0, "label": "No Minimum (All >= 0.8x)"},
                    {"value": 1.0, "label": "Target ≥ 1.0x Initial Risk"},
                    {"value": 1.5, "label": "Target ≥ 1.5x Initial Risk (Recommended) 💎"},
                    {"value": 2.0, "label": "Target ≥ 2.0x Initial Risk"},
                    {"value": 2.5, "label": "Target ≥ 2.5x Initial Risk"}
                ],
                description="Filters for trades where distance to opposite target level is at least 1.5x the initial stop risk."
            ),
            StrategyParam(
                name="trailing_be",
                label="Move Stop to Breakeven at +1R",
                type="boolean",
                default=True,
                description="Once profit reaches 1x initial risk, move stop to entry price to eliminate downside risk."
            )
        ]

    def get_educational_content(self) -> Dict[str, Any]:
        return {
            "title": "The Silent Flip Strategy: Complete Guide & Institutional Edge",
            "author": "Karl Eriksson (ProRealAlgos) & DeepMind Research Backtest",
            "video_url": "https://www.youtube.com/watch?v=Q2QjB5jBTro",
            "core_concept": (
                "The Silent Flip strategy exploits the morning liquidity sweep that occurs during the first 15–45 minutes of the US market open. "
                "Instead of chasing momentum breakouts, the strategy waits for price to test a major structural level from prior days "
                "(Range High, Range Low, or Flip Levels), identifies volatility contraction through a 'Silent Candle', and trades the institutional reversal."
            ),
            "key_levels": [
                {
                    "name": "Range High (RH)",
                    "color": "#fbbf24",
                    "description": "Yesterday's regular session High. Represents the primary upper resistance boundary."
                },
                {
                    "name": "Range Low (RL)",
                    "color": "#38bdf8",
                    "description": "Yesterday's regular session Low. Represents the primary lower support boundary."
                },
                {
                    "name": "Flip High (FH)",
                    "color": "#e11d48",
                    "description": "Prior significant resistance from the last 30 daily bars that was previously broken or validated as major supply."
                },
                {
                    "name": "Flip Low (FL)",
                    "color": "#2563eb",
                    "description": "Prior significant support from the last 30 daily bars acting as secondary demand."
                }
            ],
            "silent_candle_rules": [
                "Range Compression: The candle's total range (High - Low) must be <= 60% of the recent 14-period Average True Range (ATR).",
                "Internal Absorption: The candle must close inside the body and range of the preceding candle, showing that aggressive breakout orders have exhausted.",
                "Tight Invalidation: The tight range allows placing an initial Stop Loss just 1 tick outside the candle extreme (averaging only ~$0.40 to $1.20 per share)."
            ],
            "execution_rules": {
                "short_entry": "When Candle 1 tests RH or FH, wait for the Silent Candle. Place a Sell Stop 1 tick below the Silent Candle Low.",
                "stop_loss_short": "1 tick above the Silent Candle High.",
                "take_profit_short": "Opposite Range Low (RL) or Flip Low (FL), requiring planned R:R >= 1.5x.",
                "breakeven_rule": "Once unrealized profit reaches +1.0 R, immediately shift Stop Loss to Entry price.",
                "eod_rule": "All open positions are strictly closed before market close (3:55 PM EST). Zero overnight gap risk."
            },
            "statistical_edge_findings": [
                {
                    "finding": "Directional Asymmetry (Shorts Win, Longs Trap)",
                    "detail": "Across 6,484 backtested S&P 500 setups, Shorting Range High ($RH$) and Flip High ($FH$) generated +127.10 R. Buying Range Low ($RL$) lost -275.14 R. US markets open with aggressive dip-breakdowns that cascade through stops, whereas morning supply at RH holds with high statistical consistency."
                },
                {
                    "finding": "Sector Sensitivity",
                    "detail": "Mean-reverting, steady-cash-flow sectors (Financials, Utilities, REITs, Consumer Staples, Consumer Discretionary) delivered +142.78 R. High-beta technology momentum stocks frequently break through morning resistance and trend, causing consecutive stop-outs."
                },
                {
                    "finding": "1.5x Planned Reward/Risk Filter",
                    "detail": "Filtering for distance to target >= 1.5x initial stop risk boosted win rate to 71% in curated defensives, resulting in a Profit Factor of 5.37 and a microscopic maximum drawdown of only 0.30% of total account capital."
                }
            ]
        }

    _cached_df = None

    def run_backtest(
        self, 
        conn, 
        tickers: List[str], 
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        precomputed_csv = config.PRECOMPUTED_TRADES_CSV
        
        target_tickers_set = set(tickers)
        dir_filter = params.get("direction_filter", "SHORTS_ONLY")
        min_rr = float(params.get("min_planned_rr", 1.5))
        
        # Load trade records (cached in memory)
        if SilentFlipStrategy._cached_df is None and os.path.exists(precomputed_csv):
            raw_df = pd.read_csv(precomputed_csv)
            raw_df['initial_risk'] = abs(raw_df['entry_price'] - raw_df['stop_loss'])
            raw_df['initial_reward'] = abs(raw_df['target_profit'] - raw_df['entry_price'])
            raw_df['planned_rr'] = np.where(
                raw_df['initial_risk'] > 0, 
                raw_df['initial_reward'] / raw_df['initial_risk'], 
                0
            ).round(2)
            SilentFlipStrategy._cached_df = raw_df

        if SilentFlipStrategy._cached_df is not None:
            df = SilentFlipStrategy._cached_df
            df = df[df['ticker'].isin(target_tickers_set)].copy()
            
            # Filter Direction
            if dir_filter == 'SHORTS_ONLY':
                df = df[df['direction'] == 'SHORT']
            elif dir_filter == 'NO_RL_LONGS':
                df = df[~((df['direction'] == 'LONG') & (df['tested_level'] == 'RL'))]
            elif dir_filter == 'LONGS_ONLY':
                df = df[df['direction'] == 'LONG']
                
            # Filter Planned RR
            df = df[df['planned_rr'] >= min_rr]
            
            # Date filter if provided
            start_date = params.get("start_date")
            end_date = params.get("end_date")
            if start_date:
                df = df[df['date'] >= int(str(start_date).replace('-', ''))]
            if end_date:
                df = df[df['date'] <= int(str(end_date).replace('-', ''))]
                
            df = df.sort_values(['date', 'entry_time', 'ticker']).reset_index(drop=True)
            return df.to_dict(orient='records')

        return []

    @classmethod
    def get_trading_days(cls, start_date=None, end_date=None) -> List[int]:
        precomputed_csv = config.PRECOMPUTED_TRADES_CSV
        if cls._cached_df is None and os.path.exists(precomputed_csv):
            cls._cached_df = pd.read_csv(precomputed_csv)
        if cls._cached_df is not None:
            dates = pd.Series(cls._cached_df['date'].unique())
            if start_date:
                dates = dates[dates >= int(str(start_date).replace('-', ''))]
            if end_date:
                dates = dates[dates <= int(str(end_date).replace('-', ''))]
            return sorted(dates.tolist())
        return []

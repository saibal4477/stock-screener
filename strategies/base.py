from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

class StrategyParam(BaseModel):
    name: str
    label: str
    type: str  # 'select', 'number', 'boolean', 'slider'
    default: Any
    options: Optional[List[Dict[str, Any]]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None
    description: Optional[str] = None

class BaseStrategy(ABC):
    @property
    @abstractmethod
    def id(self) -> str:
        """Unique strategy identifier (e.g., 'silent_flip')"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name"""
        pass

    @property
    @abstractmethod
    def category(self) -> str:
        """Category: 'intraday', 'swing', 'mean_reversion', etc."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Brief strategy overview"""
        pass

    @property
    @abstractmethod
    def default_parameters(self) -> List[StrategyParam]:
        """Configurable strategy parameters for UI"""
        pass

    @abstractmethod
    def get_educational_content(self) -> Dict[str, Any]:
        """Educational documentation, rule specifications, and diagrams"""
        pass

    @abstractmethod
    def run_backtest(
        self, 
        conn, 
        tickers: List[str], 
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Executes strategy logic on ticker list.
        Returns a list of raw trade execution dicts:
        [{
           'ticker': str, 'date': int/str, 'direction': 'SHORT'|'LONG',
           'tested_level': str, 'level_price': float,
           'entry_time': int, 'entry_price': float,
           'stop_loss': float, 'target_profit': float,
           'exit_time': int, 'exit_price': float,
           'exit_reason': str, 'r_multiple': float, 'return_pct': float
        }]
        """
        pass

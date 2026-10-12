import os
import importlib
import inspect
from typing import Dict, List
from strategies.base import BaseStrategy

STRATEGY_REGISTRY: Dict[str, BaseStrategy] = {}

def register_strategy(strategy_cls):
    instance = strategy_cls()
    STRATEGY_REGISTRY[instance.id] = instance
    return strategy_cls

def load_strategies():
    current_dir = os.path.dirname(__file__)
    for filename in os.listdir(current_dir):
        if filename.endswith(".py") and not filename.startswith("__") and filename != "base.py":
            module_name = f"strategies.{filename[:-3]}"
            try:
                module = importlib.import_module(module_name)
                for item_name in dir(module):
                    item = getattr(module, item_name)
                    if (
                        inspect.isclass(item) 
                        and issubclass(item, BaseStrategy) 
                        and item is not BaseStrategy
                    ):
                        instance = item()
                        STRATEGY_REGISTRY[instance.id] = instance
            except Exception as e:
                print(f"Error loading strategy module {module_name}: {e}")

# Initial auto-load
load_strategies()

def get_strategy(strategy_id: str) -> BaseStrategy:
    if strategy_id not in STRATEGY_REGISTRY:
        load_strategies()
    return STRATEGY_REGISTRY.get(strategy_id)

def list_strategies() -> List[Dict[str, str]]:
    if not STRATEGY_REGISTRY:
        load_strategies()
    return [
        {
            "id": s.id,
            "name": s.name,
            "category": s.category,
            "description": s.description
        }
        for s in STRATEGY_REGISTRY.values()
    ]

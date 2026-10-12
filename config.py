import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Data & Config Directories
DATA_DIR = os.path.join(BASE_DIR, "data")
CHARTS_DIR = os.path.join(BASE_DIR, "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

# Databases
DB_PATH_15M = os.path.join(BASE_DIR, "screener_core_15min.db")
DB_PATH_5M = os.path.join(BASE_DIR, "screener.db")

FALLBACK_DB_15M = r"C:\Users\saiba\.gemini\antigravity\scratch\mcp-stock-screener\screener_core_15min.db"
FALLBACK_DB_5M = r"C:\Users\saiba\.gemini\antigravity\scratch\mcp-stock-screener\screener.db"

# GCS Mount Support (Cloud Run Volume Mounts)
GCS_MOUNT_DIR = os.environ.get("GCS_MOUNT_DIR", "/mnt/gcs")
GCS_MOUNT_DB_15M = os.path.join(GCS_MOUNT_DIR, "screener_core_15min.db")
GCS_MOUNT_DB_5M = os.path.join(GCS_MOUNT_DIR, "screener.db")

if os.path.exists(GCS_MOUNT_DB_15M):
    ACTIVE_DB = GCS_MOUNT_DB_15M
elif os.path.exists(GCS_MOUNT_DB_5M):
    ACTIVE_DB = GCS_MOUNT_DB_5M
elif os.path.exists(DB_PATH_15M):
    ACTIVE_DB = DB_PATH_15M
elif os.path.exists(DB_PATH_5M):
    ACTIVE_DB = DB_PATH_5M
elif os.path.exists(FALLBACK_DB_15M):
    ACTIVE_DB = FALLBACK_DB_15M
elif os.path.exists(FALLBACK_DB_5M):
    ACTIVE_DB = FALLBACK_DB_5M
else:
    ACTIVE_DB = None

# Precomputed trades dataset
PRECOMPUTED_TRADES_CSV = os.path.join(DATA_DIR, "silent_flip_trades_sp500_6m.csv")
if not os.path.exists(PRECOMPUTED_TRADES_CSV):
    PRECOMPUTED_TRADES_CSV = r"C:\Users\saiba\.gemini\antigravity\scratch\silent_flip_trades_sp500_6m.csv"

# Curated High-Edge 30 Watchlist
HIGH_EDGE_30 = [
    'REG', 'HIG', 'CNP', 'SBUX', 'PCG', 'BAC', 'EXR', 'XEL', 'L', 'MNST',
    'PFG', 'DLR', 'AFL', 'AEE', 'HBAN', 'MAA', 'TRGP', 'ALL', 'UDR', 'TJX',
    'ETR', 'CL', 'CVNA', 'EL', 'MAR', 'INVH', 'EG', 'WM', 'HAS', 'DIS'
]

# Web Server Settings
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000

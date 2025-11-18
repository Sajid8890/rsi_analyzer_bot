import os

# --- Define the base assets directory ---
ASSETS_DIR = 'assets'
DB_DIR = os.path.join(ASSETS_DIR, 'database' )
JSON_DIR = os.path.join(ASSETS_DIR, 'jsons')

# --- Master switch for live trading ---
LIVE_TRADING_ENABLED = False # SET TO True TO EXECUTE REAL TRADES
# LIVE_TRADING_ENABLED = True # SET TO True TO EXECUTE REAL TRADES

# --- Binance API Credentials (REPLACE WITH YOURS) ---
BINANCE_API_KEY = "Hn6PCZPeqHXK55c3IuTqn3tIfmdClMkACTdhA3a3z58lY2oazFkHvKGeP4KpKS1F"
BINANCE_API_SECRET = "ZW6XPWJ9EyyYfoP3aqVmFkE49Kaj5bT29zJxaDdwrzk6V65FChdE4XcycHt0r9kY"

# --- Trading Parameters ---
# This is now the BASE (minimum) threshold. The bot will raise it dynamically if needed.
RSI_HOT_COIN_THRESHOLD = 10
RSI_LENGTH = 14
RSI_ALERT_THRESHOLD = 95
MAX_OPEN_TRADES = 10
LEVERAGE = 2
TRADE_CLOSE_RSI = 65
TRADE_STAY_OPEN_HOURS = 5
LOSS_TRADES_LIMIT_24H = 1
GLOBAL_PAUSE_DURATION_HOURS = 24

# --- User Interface Settings ---
HIDE_COOLDOWN_DETAILS = True

# --- User Trade Settings ---
TRADE_AMOUNT_TYPE = "fixed_usdt"
TRADE_AMOUNT_FIXED_USDT = 3
TRADE_AMOUNT_PERCENTAGE = 10
TAKE_PROFIT_PERCENT = 10

# --- ATH Pullback Check ---
ATH_PULLBACK_THRESHOLD_PERCENT = 5

# --- Freshness Detection Strategy Parameters ---
STALE_SIGNAL_LOOKBACK_HOURS = 12
STALE_SIGNAL_PULLBACK_PERCENT = 100

# --- Cooldown for newly launched coins ---
NEW_COIN_COOLDOWN_DAYS = 5

# --- Timing and Refresh Rates ---
WEBSOCKET_REFRESH_SECONDS = 5
RSI_REFRESH_SECONDS = 1
TRADE_MONITOR_INTERVAL_SECONDS = 1
UI_REFRESH_SECONDS = 1

# --- Cooldown ---
DEFAULT_COOLDOWN_PERIOD = 172800

# --- Email (Placeholder) ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_SENDER = "dearbeeta@gmail.com"
EMAIL_PASSWORD = "phhdqdzmmuvkzrod"
EMAIL_RECEIVER = "dearbeeta@gmail.com"

# --- File Paths ---
DATABASE_FILE = os.path.join(DB_DIR, 'database.db')
COOLDOWN_DATABASE_FILE = os.path.join(DB_DIR, 'cooldowned_coins.db')
ALERT_COUNTER_FILE = os.path.join(JSON_DIR, 'alert_counter.json')
STATS_FILE = os.path.join(JSON_DIR, 'global_stats.json')
UPTIME_FILE = os.path.join(JSON_DIR, 'uptime_stats.json')
PORTFOLIO_FILE = os.path.join(JSON_DIR, 'portfolio.json')
STYLE_CONFIG_FILE = os.path.join(JSON_DIR, 'style_config.json')
RSI_PEAK_TRACKER_FILE = os.path.join(JSON_DIR, 'rsi_peak_tracker.json')

# --- Default Values ---
DEFAULT_PORTFOLIO_BALANCE = 1000.0
DEFAULT_STYLES = {
    "body_max_width": "2800px",
    "market_table_max_height": "800px"
}

# --- Flask Server ---
SERVER_HOST = '0.0.0.0'
SERVER_PORT = 5000

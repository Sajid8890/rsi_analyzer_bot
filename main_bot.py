import atexit
import threading
import time
import os
import config

# --- Core Components ---
from core.event_bus import EventBus
from core.state_manager import StateManager

# --- Persistence ---
from persistence.file_manager import FileManager
from persistence.database_manager import DatabaseManager

# --- Services ---
from services.websocket_service import WebSocketService
from services.rsi_service import RsiService
from services.trading_service import TradingService
from services.email_service import EmailService

# --- Web Server ---
from web.server import create_flask_app

def setup_directories():
    """Creates the necessary data directories if they don't exist."""
    os.makedirs(config.ASSETS_DIR, exist_ok=True)
    os.makedirs(config.DB_DIR, exist_ok=True)
    os.makedirs(config.JSON_DIR, exist_ok=True)

# --- Initialize all components ---
setup_directories()
event_bus = EventBus()
state_manager = StateManager(event_bus)
file_manager = FileManager(state_manager, event_bus)
db_manager = DatabaseManager(state_manager, event_bus)

# Load non-trade state first
file_manager.load_all()
db_manager.load_state_from_database() # This now only loads paper trades

atexit.register(file_manager.save_all_on_exit)

# Keep references to services that need to be passed to the web app
websocket_service = WebSocketService(state_manager, event_bus)
# The TradingService now handles its own initial sync (balance + positions)
trading_service = TradingService(state_manager, event_bus, db_manager)

services = [
    websocket_service,
    RsiService(state_manager, event_bus),
    trading_service, # Use the instance
    EmailService(state_manager, event_bus)
]

# --- Create the Flask app instance ---
# This is now in the global scope so gunicorn can find it
app = create_flask_app(state_manager, db_manager, websocket_service, trading_service)

def main():
    """The main entry point for the application."""
    
    event_bus_thread = threading.Thread(target=event_bus.process_events, daemon=True)
    event_bus_thread.start()
    
    for service in services:
        service.start()
        
    # This block will only run when you execute the script directly (e.g., locally)
    # It will NOT run when gunicorn imports the 'app' object
    app.run(host=config.SERVER_HOST, port=config.SERVER_PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()

import websocket
import json
import time
import config
import threading
import requests
from .base_service import BaseService

class WebSocketService(BaseService):
    """
    Manages the WebSocket connection to Binance for live market data.
    """
    def __init__(self, state_manager, event_bus):
        super().__init__(state_manager, event_bus)
        self.ws_app = None
        self.last_message_time = None
        self.monitoring_thread = None
        self.stop_monitoring = threading.Event()
        self.session = requests.Session()
        self.valid_futures_symbols = set()
        self.fetch_listing_times() # Initial fetch
        # --- NEW: Start a thread to periodically refresh the symbol list ---
        self.refresher_thread = threading.Thread(target=self._periodically_refresh_symbols, daemon=True)
        self.refresher_thread.start()

    def _periodically_refresh_symbols(self):
        """Runs in the background to refresh the exchange info every hour."""
        while True:
            # Wait for 1 hour before the next refresh
            time.sleep(3600)
            print("--- [Hourly Task] Refreshing exchange info and valid symbols... ---")
            self.fetch_listing_times()

    def fetch_listing_times(self):
        """
        Fetches exchange information to get listing times, trading rules,
        and to build a set of valid futures symbols.
        """
        print("--- Fetching coin listing times and exchange info from Binance... ---")
        try:
            response = self.session.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10 )
            response.raise_for_status()
            data = response.json()

            listing_times = {}
            # Build the set of valid symbols during the fetch
            self.valid_futures_symbols.clear()
            for symbol_info in data['symbols']:
                # Ensure it's a USDT perpetual contract
                if symbol_info['symbol'].endswith('USDT') and symbol_info.get('contractType') == 'PERPETUAL':
                    self.valid_futures_symbols.add(symbol_info['symbol'])
                    if 'onboardDate' in symbol_info:
                        # Convert milliseconds to seconds
                        listing_times[symbol_info['symbol']] = symbol_info['onboardDate'] / 1000

            self.state.update_listing_times(listing_times)
            print(f"--- Successfully fetched info for {len(self.valid_futures_symbols)} valid futures symbols. ---")

            # Publish the full data for other services (like BinanceTrader) to consume
            self.event_bus.publish('EXCHANGE_INFO_UPDATED', data)

        except requests.RequestException as e:
            print(f"🚨 CRITICAL: Could not fetch exchange info from Binance API. Error: {e}")
        except Exception as e:
            print(f"🚨 An unexpected error occurred while fetching exchange info: {e}")

    def run(self):
        print("--- WebSocket Service started. ---")
        self.stop_monitoring.clear()
        self.monitoring_thread = threading.Thread(target=self._monitor_connection, daemon=True)
        self.monitoring_thread.start()

        while True:
            self.state.controls["websocket_enabled"].wait()
            print("--- Starting WebSocket connection... ---")
            self.last_message_time = time.time()
            self.ws_app = websocket.WebSocketApp(
                "wss://fstream.binance.com/ws/!ticker@arr",
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            self.ws_app.run_forever()
            print(f"--- WebSocket connection closed. Reconnecting in {config.WEBSOCKET_REFRESH_SECONDS} seconds... ---")
            time.sleep(config.WEBSOCKET_REFRESH_SECONDS)

    def _monitor_connection(self):
        print("--- WebSocket Health Monitor started. ---")
        while not self.stop_monitoring.is_set():
            if self.last_message_time:
                time_since_last_message = time.time() - self.last_message_time
                if time_since_last_message > 30:
                    print("!!! WebSocket STALE: No message received in 30s. Forcing reconnection. !!!")
                    self.last_message_time = time.time()
                    if self.ws_app:
                        self.ws_app.close()
            time.sleep(10)

    def on_message(self, ws, message):
        """Callback for when a new message is received from the WebSocket."""
        self.last_message_time = time.time()
        if not self.state.controls["websocket_enabled"].is_set():
            if self.ws_app: self.ws_app.close()
            return
        
        try:
            data_list = json.loads(message)
            
            # Filter the data to only include valid futures symbols from our refreshed set
            valid_data = [
                item for item in data_list 
                if item.get('s') in self.valid_futures_symbols
            ]
            
            if valid_data:
                self.state.update_market_data(valid_data)

        except json.JSONDecodeError:
            print("🚨 WebSocket: Could not decode JSON message.")
        except Exception as e:
            print(f"🚨 WebSocket: Error processing message: {e}")

    def on_error(self, ws, error):
        if "Connection is already closed" not in str(error):
            print(f"!!! WebSocket Error: {error} !!!")

    def on_close(self, ws, close_status_code, close_msg):
        print(f"!!! WebSocket Connection Closed (Code: {close_status_code}, Msg: {close_msg}) !!!")

    def on_open(self, ws):
        print("--- WebSocket Connection Opened ---")
        self.last_message_time = time.time()

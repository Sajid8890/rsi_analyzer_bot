import time
import requests
import pandas as pd
import pandas_ta as ta
import config
from .base_service import BaseService

class RsiService(BaseService):
    """
    Fetches RSI values for relevant coins and publishes them.
    """
    def __init__(self, state_manager, event_bus):
        super().__init__(state_manager, event_bus)
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})

    def run(self):
        print("--- RSI Service started. ---")
        time.sleep(10) # Initial delay to allow market data to populate
        while True:
            self.state.controls["rsi_enabled"].wait()
            try:
                symbols_to_check = self.state.get_symbols_to_monitor()

                if not symbols_to_check:
                    self.state.set_rsi_status("idle", "No coins to check.")
                    time.sleep(10)
                    continue

                status_msg_prefix = f"Scanning {len(symbols_to_check)} coins"

                for i, symbol in enumerate(symbols_to_check):
                    if not self.state.controls["rsi_enabled"].is_set():
                        self.state.set_rsi_status("paused", "RSI service paused by user.")
                        break
                    
                    self.state.set_rsi_status("active", f"{status_msg_prefix} ({i+1}/{len(symbols_to_check)})", symbol)
                    
                    rsi_value = self._fetch_rsi_with_retries(symbol)
                    
                    if rsi_value is not None:
                        self.state.update_rsi_value(symbol, rsi_value)
                    
                    time.sleep(0.05)

                if self.state.controls["rsi_enabled"].is_set():
                    self.state.set_rsi_status("idle", "Cycle complete. Waiting...")
                
                time.sleep(config.RSI_REFRESH_SECONDS)

            except Exception as e:
                print(f"🚨 CRITICAL ERROR in RSI Service main loop: {e}")
                self.state.set_rsi_status("error", f"Critical Error: {e}")
                time.sleep(30)

    def _fetch_rsi_with_retries(self, symbol, max_retries=3):
        """
        Fetches RSI, but retries with exponential backoff if it fails.
        Handles new coins with insufficient data.
        """
        base_delay = 5
        for attempt in range(max_retries):
            try:
                params = {'symbol': symbol, 'interval': '1h', 'limit': 100}
                response = self.session.get("https://fapi.binance.com/fapi/v1/klines", params=params, timeout=10  )
                
                if response.status_code == 429:
                    print(f"RSI Fetch: Rate limited by Binance API. Waiting for {response.headers.get('Retry-After', 60)}s.")
                    time.sleep(int(response.headers.get('Retry-After', 60)))
                    continue
                elif response.status_code != 200:
                    return None

                klines = response.json()
                
                if not klines or len(klines) < config.RSI_LENGTH:
                    return 'New_Coin'

                df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
                df['close'] = pd.to_numeric(df['close'])
                
                rsi_series = df.ta.rsi(length=config.RSI_LENGTH)
                
                # --- CORRECTED LOGIC HERE ---
                # This is the fix. We check if the series is valid and the last value is not NaN.
                if rsi_series is not None and not rsi_series.empty and pd.notna(rsi_series.iloc[-1]):
                    return rsi_series.iloc[-1]
                else:
                    # This case might happen if the calculation still fails for other reasons.
                    return None

            except requests.exceptions.RequestException as e:
                print(f"RSI Fetch: Network error for {symbol}: {e}")
            except Exception as e:
                # This will now correctly catch the pandas error if it ever happens again, but the fix should prevent it.
                print(f"RSI Fetch: An unexpected error occurred processing {symbol}: {e}")
                return None

            delay = base_delay * (2 ** attempt)
            print(f"RSI Fetch: Will retry {symbol} in {delay} seconds... (Attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)

        print(f"RSI Fetch: All {max_retries} retries failed for {symbol}. Skipping for this cycle.")
        return None

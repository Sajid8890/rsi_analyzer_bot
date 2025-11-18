import time
import math
import threading
from .base_service import BaseService
import config
from binance.client import Client
from binance.exceptions import BinanceAPIException

class BinanceTrader:
    """Handles all live trading interactions with the Binance API."""
    def __init__(self, event_bus):
        self.client = None
        self.is_authenticated = False
        self.exchange_info = None
        self.symbol_info = {}
        self.event_bus = event_bus
        self.rules_loaded = threading.Event() # <-- NEW: Event to signal that rules are loaded
        self._initialize_client()
        self._subscribe_to_events()

    def _subscribe_to_events(self):
        """Subscribes to events relevant to the trader."""
        self.event_bus.subscribe('EXCHANGE_INFO_UPDATED', self.handle_exchange_info_update)

    def handle_exchange_info_update(self, new_info):
        """Updates the internal exchange_info when the event is published."""
        print("--- BinanceTrader: Received updated exchange info. Refreshing symbol rules. ---")
        self.exchange_info = new_info
        self.symbol_info.clear()
        self.rules_loaded.set() # <-- NEW: Signal that the rules are now loaded and ready
        print("--- BinanceTrader: Symbol rules Refreshed ---")

    def _initialize_client(self):
        if not config.LIVE_TRADING_ENABLED:
            print("--- Live trading is disabled. Binance client not initialized. ---")
            return
        
        if not config.BINANCE_API_KEY or config.BINANCE_API_KEY == "YOUR_API_KEY_HERE":
            print("🚨 CRITICAL: Binance API Key is not set. Live trading will fail.")
            return

        try:
            self.client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)
            self.is_authenticated = True
            print("--- Binance client initialized and authenticated successfully. ---")
        except BinanceAPIException as e:
            print(f"🚨 CRITICAL: Binance API authentication failed: {e}.")
            self.is_authenticated = False
        except Exception as e:
            print(f"🚨 CRITICAL: An unexpected error occurred during Binance client initialization: {e}")
            self.is_authenticated = False

    def get_futures_balance(self):
        if not self.is_authenticated: return 0.0
        try:
            account_info = self.client.futures_account_balance()
            for asset in account_info:
                if asset['asset'] == 'USDT':
                    return float(asset['availableBalance'])
            return 0.0
        except BinanceAPIException as e:
            print(f"🚨 Could not fetch futures wallet balance: {e}")
            return 0.0
        except Exception as e:
            print(f"🚨 An unexpected error occurred while fetching balance: {e}")
            return 0.0

    def get_open_positions(self):
        if not self.is_authenticated: return []
        try:
            positions = self.client.futures_position_information()
            open_positions = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
            return open_positions
        except BinanceAPIException as e:
            print(f"🚨 Could not fetch open positions: {e}")
            return []
        except Exception as e:
            print(f"🚨 An unexpected error occurred while fetching positions: {e}")
            return []

    def close_live_trade(self, symbol):
        if not self.is_authenticated: return False, 0.0
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            position_info = self.client.futures_position_information(symbol=symbol)
            if not position_info or float(position_info[0]['positionAmt']) == 0:
                return True, 0.0
            position_amt = float(position_info[0]['positionAmt'])
            side = Client.SIDE_BUY if position_amt < 0 else Client.SIDE_SELL
            quantity = abs(position_amt)
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type=Client.ORDER_TYPE_MARKET,
                quantity=quantity, reduceOnly=True
            )
            time.sleep(0.5)
            filled_order = self.client.futures_get_order(symbol=symbol, orderId=order['orderId'])
            close_price = float(filled_order.get('avgPrice', 0.0))
            return True, close_price
        except BinanceAPIException as e:
            print(f"🚨 LIVE CLOSE ERROR for {symbol}: {e}")
            return False, 0.0
        except Exception as e:
            print(f"🚨 An unexpected error occurred during live close for {symbol}: {e}")
            return False, 0.0

    def get_live_pnl(self, symbol):
        if not self.is_authenticated: return 0.0, 0.0
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                position = positions[0]
                unrealized_pnl = float(position.get('unRealizedProfit', 0.0))
                initial_margin = float(position.get('initialMargin', 0.0))
                
                if initial_margin > 0:
                    pnl_percent = (unrealized_pnl / initial_margin) * 100
                    return unrealized_pnl, pnl_percent
            return 0.0, 0.0
        except BinanceAPIException as e:
            if e.code == -2015 and not hasattr(self, '_logged_pnl_error'):
                print(f"🚨 PERMISSIONS ERROR: Could not fetch live PNL. Check API key permissions and IP whitelist. Error: {e}")
                self._logged_pnl_error = True
            elif e.code != -2015:
                 print(f"🚨 Could not fetch live PNL for {symbol}: {e}")
            return 0.0, 0.0
        except Exception as e:
            print(f"🚨 Unexpected error fetching live PNL for {symbol}: {e}")
            return 0.0, 0.0

    def _get_symbol_info(self, symbol):
        if symbol in self.symbol_info: return self.symbol_info[symbol]
        if not self.exchange_info:
            print(f"--- Exchange info not available yet. Cannot get rules for {symbol}. ---")
            return None
        try:
            info = next(s for s in self.exchange_info['symbols'] if s['symbol'] == symbol)
            self.symbol_info[symbol] = info
            return info
        except StopIteration:
            print(f"--- Could not find exchange info for {symbol}. It may not be a valid futures pair. ---")
            return None

    def _format_quantity(self, symbol, quantity):
        info = self._get_symbol_info(symbol)
        if not info: return round(quantity, 3)
        precision = info.get('quantityPrecision')
        if precision is None: return round(quantity, 3)
        factor = 10 ** precision
        return math.floor(quantity * factor) / factor

    def _format_price(self, symbol, price):
        info = self._get_symbol_info(symbol)
        if not info: return round(price, 4)
        price_filter = next((f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
        if not price_filter or 'tickSize' not in price_filter: return round(price, 4)
        tick_size = float(price_filter['tickSize'])
        precision = int(round(-math.log(tick_size, 10), 0))
        return round(price, precision)

    def execute_short_trade(self, symbol, usdt_amount, leverage, take_profit_percent):
        if not self.is_authenticated: return None, None, "Not Authenticated"
        
        # --- MODIFIED: Wait until rules are loaded before executing a trade ---
        if not self.rules_loaded.is_set():
            print(f"--- Waiting for exchange rules to be loaded before trading {symbol}... ---")
            self.rules_loaded.wait(timeout=30) # Wait up to 30 seconds
            if not self.rules_loaded.is_set():
                return None, None, "Timed out waiting for exchange rules."

        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            symbol_info = self._get_symbol_info(symbol)
            if not symbol_info:
                return None, None, f"Invalid futures symbol or info not found."
            min_notional_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None)
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])
            raw_quantity = (usdt_amount * leverage) / price
            quantity = self._format_quantity(symbol, raw_quantity)
            notional_value = quantity * price
            if min_notional_filter and notional_value < float(min_notional_filter['notional']):
                error_msg = f"Order size ({notional_value:.2f} USDT) is less than the minimum required ({min_notional_filter['notional']} USDT)."
                return None, None, error_msg
            if quantity <= 0:
                return None, None, "Calculated quantity is zero."
            order = self.client.futures_create_order(symbol=symbol, side=Client.SIDE_SELL, type=Client.ORDER_TYPE_MARKET, quantity=quantity)
            time.sleep(0.5)
            filled_order = self.client.futures_get_order(symbol=symbol, orderId=order['orderId'])
            entry_price_actual = float(filled_order['avgPrice'])
            if entry_price_actual == 0: entry_price_actual = price
            raw_tp_price = entry_price_actual * (1 - (take_profit_percent / 100) / leverage)
            tp_price = self._format_price(symbol, raw_tp_price)
            tp_order = self.client.futures_create_order(symbol=symbol, side=Client.SIDE_BUY, type='TAKE_PROFIT_MARKET', stopPrice=tp_price, closePosition=True)
            return filled_order, tp_order, None
        except BinanceAPIException as e:
            error_message = f"Binance API Error: Code={e.code}, Msg={e.message}"
            return None, None, error_message
        except Exception as e:
            error_message = f"Unexpected Error: {str(e)}"
            return None, None, error_message

class TradingService(BaseService):
    """The core trading logic engine."""
    def __init__(self, state_manager, event_bus, db_manager):
        super().__init__(state_manager, event_bus)
        self.db_manager = db_manager
        self.binance_trader = BinanceTrader(event_bus)
        self._subscribe_to_events()
        self.is_synced = False
        self.live_trade_monitor_threads = {}

    def initial_sync(self):
        if self.is_synced: return
        print("--- Starting initial sync with Binance... ---")
        # --- MODIFIED: Wait for rules to be loaded before syncing ---
        if not self.binance_trader.rules_loaded.is_set():
            print("--- Sync waiting for exchange rules to be loaded... ---")
            self.binance_trader.rules_loaded.wait(timeout=30)

        # self.update_portfolio_balance()
        # self.sync_open_positions()
        print("--- Initial sync complete. ---")
        self.is_synced = True

    # def update_portfolio_balance(self):
        # if config.LIVE_TRADING_ENABLED:
            # balance = self.binance_trader.get_futures_balance()
            # print(f"--- Fetched live futures balance: {balance:.2f} USDT ---")
            # self.state.update_portfolio_balance(balance)
        # else:
            # print("--- Live trading disabled, using portfolio balance from file. ---")

    def sync_open_positions(self):
        if not config.LIVE_TRADING_ENABLED: return
        live_positions = self.binance_trader.get_open_positions()
        if not live_positions:
            print("--- No open positions found on Binance. ---")
            return
        print(f"--- Found {len(live_positions)} open position(s) on Binance. Syncing... ---")
        with self.state.lock:
            db_trades = self.db_manager.get_open_db_trades()
            for pos in live_positions:
                symbol = pos['symbol']
                if symbol in self.state.active_trades:
                    print(f"--- {symbol} is already active in the bot. Skipping sync. ---")
                    continue
                db_trade = db_trades.get(symbol)
                pnl_usdt, pnl_percent = self.binance_trader.get_live_pnl(symbol)
                if db_trade:
                    print(f"--- Syncing {symbol}: Found matching 'Open' trade in database. Restoring state. ---")
                    self.state.restore_live_trade(
                        symbol=symbol, entry_price=float(pos.get('entryPrice', 0.0)),
                        trade_amount=db_trade.get('Trade_Amount', config.TRADE_AMOUNT_FIXED_USDT),
                        leverage=int(pos.get('leverage', config.LEVERAGE)),
                        alert_num=db_trade.get('Alert_id', 'N/A'), entry_rsi=db_trade.get('Entry_RSI', 'N/A')
                    )
                else:
                    print(f"--- Syncing {symbol}: Found external trade. Creating new record. ---")
                    alert_num = self.db_manager.get_next_alert_number()
                    entry_price = float(pos.get('entryPrice', 0.0))
                    initial_margin = float(pos.get('initialMargin', 0.0))
                    self.state.open_trade(
                        symbol=symbol, price=entry_price, rsi_value='Cant_Fetch',
                        change_24h='Cant_Fetch', source="Live", alert_number=alert_num,
                        log_message="Synced from Binance", trade_amount=initial_margin
                    )
                self.state.update_trade_pnl(symbol, pnl_percent, pnl_usdt, None)
                self._start_live_trade_monitor(symbol)

    def _subscribe_to_events(self):
        self.event_bus.subscribe('STATE_UPDATED_RSI', self.handle_rsi_update)
        self.event_bus.subscribe('TRADE_OPENED', self._handle_trade_opened)
        self.event_bus.subscribe('TRADE_CLOSED', self._handle_trade_closed)

    def run(self):
        print("--- Trading Service (Monitor) started. ---")
        time.sleep(1) # Give other services a moment to start
        self.initial_sync()
        while True:
            self.state.controls["trading_enabled"].wait()
            try:
                with self.state.lock:
                    paper_trades = {s: t for s, t in self.state.active_trades.items() if t.get('source', 'Bot').lower() != 'live'}
                if not paper_trades:
                    time.sleep(5)
                    continue
                for symbol, trade in paper_trades.items():
                    self._monitor_paper_trade(symbol, trade)
            except Exception as e:
                print(f"!!! Error in Paper Trade Monitor loop: {e} !!!")
                time.sleep(60)
            time.sleep(config.TRADE_MONITOR_INTERVAL_SECONDS)

    def _handle_trade_opened(self, trade_data):
        if trade_data.get('source') == 'Live':
            symbol = next((s for s, t in self.state.active_trades.items() if t['alert_num'] == trade_data['alert_num']), None)
            if symbol:
                self._start_live_trade_monitor(symbol)

    def _handle_trade_closed(self, close_data):
        symbol = close_data['trade_data'].get('symbol')
        if symbol and symbol in self.live_trade_monitor_threads:
            del self.live_trade_monitor_threads[symbol]
            print(f"--- Stopped PNL monitor thread for closed trade: {symbol} ---")

    def _start_live_trade_monitor(self, symbol):
        if symbol in self.live_trade_monitor_threads:
            return
        
        thread = threading.Thread(target=self._dedicated_live_monitor_loop, args=(symbol,), daemon=True)
        self.live_trade_monitor_threads[symbol] = thread
        thread.start()
        print(f"--- Started dedicated PNL monitor thread for: {symbol} ---")

    def _dedicated_live_monitor_loop(self, symbol):
        while True:
            with self.state.lock:
                if symbol not in self.state.active_trades:
                    break

            pnl_usdt, pnl_percent = self.binance_trader.get_live_pnl(symbol)
            with self.state.lock:
                current_rsi = self.state.rsi_data.get(symbol)
            
            self.state.update_trade_pnl(symbol, pnl_percent, pnl_usdt, current_rsi)
            
            if isinstance(current_rsi, (int, float)) and current_rsi <= config.TRADE_CLOSE_RSI and pnl_usdt > 0.01:
                print(f"--- Closing LIVE trade {symbol}: RSI dropped below {config.TRADE_CLOSE_RSI} while in profit. ---")
                success, close_price = self.binance_trader.close_live_trade(symbol)
                if success:
                    self.state.close_trade(symbol, f"RSI Close (<{config.TRADE_CLOSE_RSI})", close_price, current_rsi)
                    break
            
            time.sleep(1.5)

    def _monitor_paper_trade(self, symbol, trade):
        with self.state.lock:
            current_price = self.state.coin_data.get(symbol, {}).get('price')
            current_rsi = self.state.rsi_data.get(symbol)
        if not current_price: return
        price_change_percent = ((trade['entry_price'] - current_price) / trade['entry_price']) * 100
        pnl_percent = price_change_percent * trade['leverage']
        pnl_usdt = (trade['trade_amount'] * trade['leverage']) * (price_change_percent / 100)
        self.state.update_trade_pnl(symbol, pnl_percent, pnl_usdt, current_rsi)
        if not self.state.controls["global_pause_active"].is_set():
            if pnl_percent >= config.TAKE_PROFIT_PERCENT:
                self.state.close_trade(symbol, f"Target Profit (>{config.TAKE_PROFIT_PERCENT}%)", current_price, current_rsi or 0)
                return
            if isinstance(current_rsi, (int, float)) and current_rsi <= config.TRADE_CLOSE_RSI and pnl_usdt > 0:
                self.state.close_trade(symbol, f"RSI Close (<{config.TRADE_CLOSE_RSI})", current_price, current_rsi)
                return

    def handle_rsi_update(self, data):
        symbol, rsi_value = data['symbol'], data['rsi']
        if not isinstance(rsi_value, (int, float)): return
        
        with self.state.lock:
            is_in_trade = symbol in self.state.active_trades
            cooldown_info = self.state.cooldowned_coins.get(symbol)
            is_on_cooldown = cooldown_info and time.time() < cooldown_info['end_time']

        if self.state.can_open_new_trade() and rsi_value > config.RSI_ALERT_THRESHOLD and not is_in_trade and not is_on_cooldown:
            with self.state.lock:
                coin_details = self.state.coin_data.get(symbol)
                if not coin_details: return
                trade_amount = config.TRADE_AMOUNT_FIXED_USDT if config.TRADE_AMOUNT_TYPE == 'fixed_usdt' else (self.state.portfolio['balance'] * config.TRADE_AMOUNT_PERCENTAGE) / 100
                required_margin = max(trade_amount, 5.1)
                if self.state.portfolio['balance'] < required_margin:
                    if not hasattr(self, '_logged_balance_error'):
                        print(f"--- Skipping trade: Insufficient balance ({self.state.portfolio['balance']:.2f} USDT) to meet requirement of {required_margin:.2f} USDT. ---")
                        self._logged_balance_error = True
                    return
                else:
                    if hasattr(self, '_logged_balance_error'): del self._logged_balance_error

            log_message = f"RSI: {rsi_value:.2f}"
            print(f"--- Fresh Trade Candidate: {symbol} ({log_message}). Attempting to open trade. ---")
            
            if config.LIVE_TRADING_ENABLED:
                order, tp_order, error_message = self.binance_trader.execute_short_trade(symbol, trade_amount, config.LEVERAGE, config.TAKE_PROFIT_PERCENT)
                if order and tp_order:
                    entry_price = float(order['avgPrice'])
                    alert_number = self.db_manager.get_next_alert_number()
                    self.state.open_trade(symbol, entry_price, rsi_value, coin_details.get('change_24h', 0), "Live", alert_number, log_message, trade_amount=trade_amount)
                else:
                    print(f"--- LIVE trade execution FAILED for {symbol}. Reason: {error_message} ---")
                    self.event_bus.publish('ADD_TO_COOLDOWN', {
                        'symbol': symbol, 'reason': 'Live Fail',
                        'end_time': time.time() + 300
                    })
                    with self.state.lock:
                        self.state.add_alert_log(f"FAIL: {symbol}", error_message)
            else:
                alert_number = self.db_manager.get_next_alert_number()
                self.state.open_trade(symbol, coin_details['price'], rsi_value, coin_details.get('change_24h'), "Bot", alert_number, log_message)

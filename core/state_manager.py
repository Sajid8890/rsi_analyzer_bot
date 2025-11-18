import threading
import time
from datetime import datetime, timedelta
import config

class StateManager:
    """
    Manages the real-time state of the application.
    This is the single source of truth for all services.
    """
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.lock = threading.RLock()

        # --- Live Data ---
        self.coin_data = {}
        self.rsi_data = {}
        self.active_trades = {}
        self.alerted_coins = {}
        self.cooldowned_coins = {} # <-- NEW: To store cooldown data from the new DB
        self.listing_times = {}
        self.rsi_peak_tracker = {}

        # --- Statistics & Portfolio ---
        self.portfolio = {"balance": 0.0}
        self.global_stats = {"global_profit_usdt": 0.0, "global_loss_usdt": 0.0, "profitable_trades": 0, "loss_trades": 0}
        self.bot_start_time = time.time()
        self.total_uptime_seconds = 0

        # --- Control Flags ---
        self.controls = {
            "websocket_enabled": threading.Event(), "rsi_enabled": threading.Event(),
            "trading_enabled": threading.Event(), "email_enabled": threading.Event(),
            "monitor_all_coins": threading.Event(), "global_pause_active": threading.Event(),
            "trade_execution_enabled": threading.Event()
        }
        self.styles = config.DEFAULT_STYLES.copy()

        # --- Status ---
        self.rsi_status = {"status": "initializing", "message": "Waiting for initial data...", "current_coin": None}
        self.alert_log = []
        self.last_trade_execution_time = 0

        self._initialize_controls()

    def _initialize_controls(self):
        """Sets the default state for all control flags."""
        for name, event in self.controls.items():
            # By default, enable WebSocket, RSI, and monitoring.
            if name in ['websocket_enabled', 'rsi_enabled', 'monitor_all_coins']:
                event.set()
            # By default, disable everything else, including Trade Execution.
            else:
                event.clear()
        # --- MODIFIED: Initially disable trade execution ---
        self.controls['trade_execution_enabled'].clear()


    def update_portfolio_balance(self, new_balance):
        with self.lock:
            self.portfolio['balance'] = new_balance
        self.event_bus.publish('PORTFOLIO_UPDATED', None)

    def restore_live_trade(self, symbol, entry_price, trade_amount, leverage, alert_num, entry_rsi):
        with self.lock:
            if symbol in self.active_trades: return
            now = datetime.now()
            self.active_trades[symbol] = {
                'alert_num': alert_num, 'entry_price': entry_price, 'entry_time': now.timestamp(),
                'entry_rsi': entry_rsi, 'trade_amount': trade_amount, 'leverage': leverage,
                'pnl_percent': 0, 'pnl_usdt': 0, 'source': 'Live',
                'max_neg_pnl_pct': 0, 'max_neg_pnl_usdt': 0, 'max_neg_rsi': entry_rsi if isinstance(entry_rsi, (int, float)) else 0
            }

    def get_full_state_snapshot(self):
        with self.lock:
            active_trades_copy = {k: v.copy() for k, v in self.active_trades.items()}
            # --- MODIFIED: Use the new cooldowned_coins dictionary ---
            cooldowned_coins_copy = {k: v.copy() for k, v in self.cooldowned_coins.items()}

            return {
                "coin_data": self.coin_data.copy(),
                "rsi_data": self.rsi_data.copy(),
                "active_trades": active_trades_copy,
                "alerted_coins": cooldowned_coins_copy, # Return cooldowned_coins as alerted_coins for UI
                "portfolio": self.portfolio.copy(),
                "global_stats": self.global_stats.copy(),
                "bot_start_time": self.bot_start_time,
                "total_uptime_seconds": self.total_uptime_seconds,
                "controls": {name: event.is_set() for name, event in self.controls.items()},
                "styles": self.styles.copy(),
                "rsi_status": self.rsi_status.copy(),
                "alert_log": self.alert_log[:20]
            }

    def get_symbols_to_monitor(self):
        with self.lock:
            MAX_COINS_TO_MONITOR = 100
            BASE_THRESHOLD = config.RSI_HOT_COIN_THRESHOLD

            all_coins_sorted = sorted(self.coin_data.values(), key=lambda x: x.get('change_24h', 0), reverse=True)
            hot_coins_above_base = [c for c in all_coins_sorted if c.get('change_24h', 0) >= BASE_THRESHOLD]

            dynamic_threshold = BASE_THRESHOLD
            if len(hot_coins_above_base) > MAX_COINS_TO_MONITOR:
                dynamic_threshold = hot_coins_above_base[MAX_COINS_TO_MONITOR - 1].get('change_24h', BASE_THRESHOLD)

            final_hot_coins = [c['symbol'] for c in hot_coins_above_base if c.get('change_24h', 0) >= dynamic_threshold]
            
            # --- MODIFIED: Check against the new cooldowned_coins dictionary ---
            symbols_set = set(self.active_trades.keys()) | set(self.cooldowned_coins.keys()) | set(final_hot_coins)
            
            return sorted(list(symbols_set), key=lambda s: self.coin_data.get(s, {}).get('change_24h', 0), reverse=True)


    def can_open_new_trade(self):
        with self.lock:
            return (
                self.controls["trade_execution_enabled"].is_set() and
                not self.controls["global_pause_active"].is_set() and
                len(self.active_trades) < config.MAX_OPEN_TRADES and
                (time.time() - self.last_trade_execution_time) > 10
            )

    def update_market_data(self, data_list):
        with self.lock:
            for data in data_list:
                symbol = data.get('s')
                if not (symbol and symbol.endswith('USDT')): continue
                
                self.coin_data[symbol] = {
                    'symbol': symbol,
                    'price': float(data.get('c', 0)), 
                    'change_24h': float(data.get('P', 0)),
                    'high_24h': float(data.get('h', 0)),
                    'listing_time': self.listing_times.get(symbol)
                }
        self.event_bus.publish('STATE_UPDATED_MARKET')

    def update_listing_times(self, times_dict):
        with self.lock:
            self.listing_times = times_dict

    def update_rsi_value(self, symbol, rsi_value):
        with self.lock:
            self.rsi_data[symbol] = rsi_value
            current_price = self.coin_data.get(symbol, {}).get('price')

            if isinstance(rsi_value, (int, float)):
                if rsi_value > config.RSI_ALERT_THRESHOLD and current_price:
                    peak_info = self.rsi_peak_tracker.get(symbol)
                    now = time.time()
                    
                    if not peak_info or (now - peak_info.get('timestamp', 0)) > (config.STALE_SIGNAL_LOOKBACK_HOURS * 3600):
                        self.rsi_peak_tracker[symbol] = {'peak_price': current_price, 'timestamp': now}
                    elif current_price > peak_info.get('peak_price', 0):
                        peak_info['peak_price'] = current_price
                        peak_info['timestamp'] = now
        
        self.event_bus.publish('STATE_UPDATED_RSI', {'symbol': symbol, 'rsi': rsi_value})

    def open_trade(self, symbol, price, rsi_value, change_24h, source, alert_number, log_message=None, trade_amount=None):
        with self.lock:
            if symbol in self.active_trades: return False

            if trade_amount is None:
                if config.TRADE_AMOUNT_TYPE == 'percentage':
                    trade_amount = (self.portfolio['balance'] * config.TRADE_AMOUNT_PERCENTAGE) / 100
                else:
                    trade_amount = config.TRADE_AMOUNT_FIXED_USDT
            
            if source.lower() != 'live' and self.portfolio['balance'] < trade_amount:
                self.add_alert_log(f"SKIP: {symbol}", "Insufficient Funds")
                return False

            now = datetime.now()
            self.active_trades[symbol] = {
                'alert_num': alert_number, 'entry_price': price, 'entry_time': now.timestamp(),
                'entry_rsi': rsi_value, 'trade_amount': trade_amount, 'leverage': config.LEVERAGE,
                'pnl_percent': 0, 'pnl_usdt': 0, 'source': source,
                'max_neg_pnl_pct': 0, 'max_neg_pnl_usdt': 0, 'max_neg_rsi': rsi_value if isinstance(rsi_value, (int, float)) else 0
            }
            self.last_trade_execution_time = time.time()
            
            final_log_message = log_message if log_message is not None else (f"{rsi_value:.2f}" if isinstance(rsi_value, (int, float)) else rsi_value)
            self.add_alert_log(f"OPEN SHORT ({source}): {symbol}", final_log_message)

        self.event_bus.publish('TRADE_OPENED', self.active_trades[symbol].copy())
        return True

    def close_trade(self, symbol, reason, close_price, exit_rsi):
        with self.lock:
            if symbol not in self.active_trades: return
            trade_data = self.active_trades.pop(symbol)

            if trade_data.get('source', 'Bot').lower() != 'live':
                pnl_usdt = trade_data['pnl_usdt']
                self.portfolio['balance'] += pnl_usdt

                if pnl_usdt >= 0:
                    self.global_stats["global_profit_usdt"] += pnl_usdt
                    self.global_stats["profitable_trades"] += 1
                else:
                    self.global_stats["global_loss_usdt"] += abs(pnl_usdt)
                    self.global_stats["loss_trades"] += 1
            
            # --- MODIFIED: Publish an event to add the coin to cooldown ---
            cooldown_end_time = time.time() + config.DEFAULT_COOLDOWN_PERIOD
            self.event_bus.publish('ADD_TO_COOLDOWN', {
                'symbol': symbol,
                'reason': reason,
                'end_time': cooldown_end_time
            })
            self.add_alert_log(f"CLOSE SHORT: {symbol}", reason)

        self.event_bus.publish('TRADE_CLOSED', {
            'trade_data': trade_data, 'reason': reason, 'close_price': close_price,
            'exit_rsi': exit_rsi, 'new_balance': self.portfolio.get('balance', 0)
        })
        self.event_bus.publish('STATS_UPDATED')
        self.event_bus.publish('PORTFOLIO_UPDATED')


    def update_trade_pnl(self, symbol, pnl_percent, pnl_usdt, current_rsi):
        with self.lock:
            if symbol in self.active_trades:
                trade = self.active_trades[symbol]
                trade['pnl_percent'] = pnl_percent
                trade['pnl_usdt'] = pnl_usdt

                if current_rsi is not None and isinstance(current_rsi, (int, float)):
                    if pnl_percent < trade['max_neg_pnl_pct']:
                        trade['max_neg_pnl_pct'] = pnl_percent
                    if pnl_usdt < trade['max_neg_pnl_usdt']:
                        trade['max_neg_pnl_usdt'] = pnl_usdt
                    if current_rsi < trade['max_neg_rsi']:
                        trade['max_neg_rsi'] = current_rsi


    def add_alert_log(self, symbol, message):
        with self.lock:
            self.alert_log.insert(0, {"time": datetime.now().strftime('%H:%M:%S'), "symbol": symbol, "rsi": message})
            self.alert_log = self.alert_log[:50]

    def set_rsi_status(self, status, message, current_coin=None):
        with self.lock:
            self.rsi_status = {"status": status, "message": message, "current_coin": current_coin}

    def toggle_control(self, control_name, action):
        with self.lock:
            if control_name == 'all':
                for name, event in self.controls.items():
                    if name in ['global_pause_active']: continue
                    if action == 'pause':
                        event.clear()
                    else:
                        event.set()
            elif control_name in self.controls:
                if control_name == 'global_pause_active' and action == 'resume':
                    self.lift_global_pause()
                elif control_name != 'global_pause_active':
                    if action == 'pause':
                        self.controls[control_name].clear()
                    else:
                        self.controls[control_name].set()

    def activate_global_pause(self, loss_count):
        with self.lock:
            if not self.controls['global_pause_active'].is_set():
                self.controls['global_pause_active'].set()
                self.controls['trade_execution_enabled'].clear()
                self.event_bus.publish('GLOBAL_PAUSE_TRIGGERED', {'loss_count': loss_count})
                print(f"--- !!! GLOBAL PAUSE ACTIVATED due to {loss_count} losses. Trading paused for {config.GLOBAL_PAUSE_DURATION_HOURS} hours. !!! ---")
                threading.Timer(config.GLOBAL_PAUSE_DURATION_HOURS * 3600, self.lift_global_pause).start()

    def lift_global_pause(self):
        with self.lock:
            if self.controls['global_pause_active'].is_set():
                self.controls['global_pause_active'].clear()
                self.event_bus.publish('GLOBAL_PAUSE_LIFTED')
                print("--- !!! GLOBAL PAUSE LIFTED. Trading can be resumed. !!! ---")

import pandas as pd
import sqlite3
import os
import json
import time
from datetime import datetime, timedelta
import threading
import config

class DatabaseManager:
    """Handles all read/write operations for the trade database (SQLite file)."""
    def __init__(self, state_manager, event_bus):
        self.state = state_manager
        self.event_bus = event_bus
        self.db_lock = threading.RLock()
        self.db_path = config.DATABASE_FILE
        self.cooldown_db_path = config.COOLDOWN_DATABASE_FILE # <-- NEW
        self.table_name = 'trades'
        self.cooldown_table_name = 'cooldowns' # <-- NEW
        self._initialize_db()
        self._subscribe_to_events()

    def get_open_db_trades(self):
        """Fetches all trades marked as 'Open' from the database."""
        df = self._read_db_to_df(f"SELECT * FROM {self.table_name} WHERE Status = 'Open'")
        if df.empty:
            return {}
        # Convert to a dictionary keyed by symbol for easy lookup
        return df.set_index('Symbol').to_dict('index')

    def _format_datetime(self, dt_obj):
        """Formats a datetime object into 'DD-MM-YYYY -> HH:MM:SS AM/PM'."""
        if not isinstance(dt_obj, datetime):
            return None
        return dt_obj.strftime('%d-%m-%Y -> %I:%M:%S %p')

    def _initialize_db(self):
        """Ensures the database files and tables exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Initialize main trade database
        with self.db_lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                create_table_sql = f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    Alert_id INTEGER PRIMARY KEY, Timestamp TEXT, Symbol TEXT, Type TEXT, Status TEXT,
                    Reason TEXT, Entry_Price REAL, Exit_Price REAL, PNL_pct REAL, PNL_USDT REAL,
                    Entry_RSI REAL, Exit_RSI REAL, Trade_Amount REAL, Leverage INTEGER,
                    Leveraged_Amount REAL, Change_24h_pct REAL, Source TEXT, Exit_Time TEXT,
                    Trade_Duration_Hours REAL, Cooldown_Trigger_Value REAL, max_neg_pnl_pct REAL,
                    max_neg_pnl_usdt REAL, max_neg_rsi REAL
                );
                """
                cursor.execute(create_table_sql)
                self._add_missing_columns(cursor, self.table_name)
                conn.commit()
                print(f"--- SQLite database initialized: {self.db_path} ---")
            except sqlite3.Error as e:
                print(f"🚨 CRITICAL: SQLite error during initialization: {e}")
            finally:
                if conn: conn.close()

        # Initialize cooldown database
        with self.db_lock:
            conn = None
            try:
                conn = sqlite3.connect(self.cooldown_db_path)
                cursor = conn.cursor()
                create_cooldown_table_sql = f"""
                CREATE TABLE IF NOT EXISTS {self.cooldown_table_name} (
                    symbol TEXT PRIMARY KEY,
                    entry_date REAL,
                    exit_date REAL,
                    reason TEXT
                );
                """
                cursor.execute(create_cooldown_table_sql)
                conn.commit()
                print(f"--- Cooldown database initialized: {self.cooldown_db_path} ---")
            except sqlite3.Error as e:
                print(f"🚨 CRITICAL: Cooldown DB error during initialization: {e}")
            finally:
                if conn: conn.close()


    def _add_missing_columns(self, cursor, table_name):
        """Adds new columns to the table if they are missing, for backward compatibility."""
        try:
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = [info[1] for info in cursor.fetchall()]
            
            new_columns = {
                'Exit_Time': 'TEXT', 'Trade_Duration_Hours': 'REAL', 'Cooldown_Trigger_Value': 'REAL',
                'max_neg_pnl_pct': 'REAL', 'max_neg_pnl_usdt': 'REAL', 'max_neg_rsi': 'REAL'
            }
            
            for col_name, col_type in new_columns.items():
                if col_name not in columns:
                    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type};")
                    print(f"--- Added missing column '{col_name}' to database. ---")

        except sqlite3.Error as e:
            print(f"Warning: Could not add missing columns. Error: {e}")


    def _execute_query(self, query, params=(), fetch_one=False, fetch_all=False, db_path=None):
        """Helper to execute a query with thread safety."""
        with self.db_lock:
            conn = None
            try:
                conn = sqlite3.connect(db_path or self.db_path)
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                if fetch_one: return cursor.fetchone()
                if fetch_all: return cursor.fetchall()
            except sqlite3.Error as e:
                print(f"🚨 SQLite Error: {e} in query: {query}")
                return None
            finally:
                if conn: conn.close()

    def _read_db_to_df(self, query, db_path=None):
        """Helper to read the database into a pandas DataFrame."""
        with self.db_lock:
            conn = None
            try:
                conn = sqlite3.connect(db_path or self.db_path)
                return pd.read_sql_query(query, conn)
            except (pd.io.sql.DatabaseError, sqlite3.Error) as e:
                print(f"--- Could not read from database: {e} ---")
                return pd.DataFrame()
            finally:
                if conn: conn.close()

    def _subscribe_to_events(self):
        self.event_bus.subscribe('TRADE_OPENED', self.handle_trade_opened)
        self.event_bus.subscribe('TRADE_CLOSED', self.handle_trade_closed)
        self.event_bus.subscribe('COOLDOWN_LOG_REQUESTED', self.handle_cooldown_log)
        self.event_bus.subscribe('ADD_TO_COOLDOWN', self.handle_add_to_cooldown) # <-- NEW
        self.event_bus.subscribe('REMOVE_FROM_COOLDOWN', self.handle_remove_from_cooldown) # <-- NEW

    def load_state_from_database(self):
        """Restores open PAPER trades and the cooldown list from the databases at startup."""
        print("--- Loading state from database... ---")
        
        # Load Paper Trades
        with self.state.lock:
            try:
                df = self._read_db_to_df(f"SELECT * FROM {self.table_name}")
                if not df.empty:
                    def parse_custom_date(date_str):
                        if not date_str or not isinstance(date_str, str): return None
                        try: return datetime.strptime(date_str, '%d-%m-%Y -> %I:%M:%S %p')
                        except ValueError:
                            try: return datetime.fromisoformat(date_str)
                            except (ValueError, TypeError): return None
                    df['Timestamp_dt'] = df['Timestamp'].apply(parse_custom_date)
                    open_paper_trades_df = df[(df['Status'] == 'Open') & (df['Source'] != 'Live')]
                    for _, row in open_paper_trades_df.iterrows():
                        symbol = row['Symbol']
                        timestamp_dt = row['Timestamp_dt']
                        if timestamp_dt:
                            self.state.active_trades[symbol] = {
                                'alert_num': row['Alert_id'], 'entry_price': float(row['Entry_Price']),
                                'entry_time': timestamp_dt.timestamp(), 'entry_rsi': row.get('Entry_RSI'),
                                'trade_amount': row.get('Trade_Amount', config.TRADE_AMOUNT_FIXED_USDT),
                                'leverage': row.get('Leverage', config.LEVERAGE), 'pnl_percent': 0, 'pnl_usdt': 0,
                                'source': row.get('Source', 'Bot'), 'max_neg_pnl_pct': 0, 'max_neg_pnl_usdt': 0,
                                'max_neg_rsi': row.get('Entry_RSI', 0)
                            }
                    print(f"--- Restored {len(self.state.active_trades)} open paper trade(s). ---")
            except Exception as e:
                print(f"🚨 CRITICAL: Failed to load paper trades from database. Error: {e}")

        # Load Cooldown List
        with self.state.lock:
            try:
                df_cooldown = self._read_db_to_df(f"SELECT * FROM {self.cooldown_table_name}", db_path=self.cooldown_db_path)
                now = time.time()
                active_cooldowns = df_cooldown[df_cooldown['exit_date'] > now]
                
                self.state.cooldowned_coins = {
                    row['symbol']: {'reason': row['reason'], 'end_time': row['exit_date']}
                    for _, row in active_cooldowns.iterrows()
                }
                print(f"--- Restored {len(self.state.cooldowned_coins)} active cooldown(s). ---")
            except Exception as e:
                print(f"🚨 CRITICAL: Failed to load cooldowns from database. Error: {e}")


    def get_next_alert_number(self):
        with self.db_lock:
            alert_num = 0
            try:
                if os.path.exists(config.ALERT_COUNTER_FILE):
                    with open(config.ALERT_COUNTER_FILE, 'r') as f: data = json.load(f)
                    alert_num = data.get('last_alert_number', 0)
                else:
                    result = self._execute_query(f"SELECT MAX(Alert_id) FROM {self.table_name}", fetch_one=True)
                    if result and result[0] is not None: alert_num = result[0]
            except (IOError, json.JSONDecodeError, sqlite3.Error) as e:
                print(f"Warning: Failed to read alert number, defaulting to 0. Error: {e}")
                alert_num = 0
            next_alert_num = alert_num + 1
            try:
                with open(config.ALERT_COUNTER_FILE, 'w') as f: json.dump({'last_alert_number': next_alert_num}, f)
            except IOError as e:
                print(f"🚨 CRITICAL: Could not write to alert counter file! {e}")
            return int(next_alert_num)

    def handle_trade_opened(self, trade_data):
        with self.db_lock:
            symbol = next((s for s, t in self.state.active_trades.items() if t['alert_num'] == trade_data['alert_num']), None)
            if not symbol: return
            with self.state.lock: coin_details = self.state.coin_data.get(symbol, {})
            exists = self._execute_query(f"SELECT 1 FROM {self.table_name} WHERE Alert_id = ?", (trade_data['alert_num'],), fetch_one=True)
            if exists:
                print(f"--- DB: Trade Alert #{trade_data['alert_num']} already exists. Skipping write. ---")
                return
            db_entry = {
                'Alert_id': trade_data['alert_num'], 'Timestamp': self._format_datetime(datetime.now()),
                'Symbol': symbol, 'Type': 'SHORT', 'Status': 'Open', 'Reason': None,
                'Entry_Price': trade_data['entry_price'], 'Exit_Price': None, 'PNL_pct': None, 'PNL_USDT': None,
                'Entry_RSI': trade_data['entry_rsi'], 'Exit_RSI': None, 'Trade_Amount': trade_data['trade_amount'], 
                'Leverage': trade_data['leverage'], 'Leveraged_Amount': trade_data['trade_amount'] * trade_data['leverage'],
                'Change_24h_pct': coin_details.get('change_24h', 0), 'Source': trade_data['source']
            }
            self._write_df_to_db(pd.DataFrame([db_entry]), self.table_name, self.db_path)

    def handle_trade_closed(self, close_data):
        """Updates a trade's status to 'Closed' in the database."""
        trade_data = close_data['trade_data']
        self._update_trade_in_db(
            alert_num=trade_data['alert_num'], new_status="Closed", reason=close_data['reason'],
            pnl_percent=trade_data['pnl_percent'], pnl_usdt=trade_data['pnl_usdt'],
            close_price=close_data['close_price'], exit_rsi=close_data['exit_rsi'],
            entry_time=trade_data['entry_time'],
            max_neg_pnl_pct=trade_data.get('max_neg_pnl_pct'),
            max_neg_pnl_usdt=trade_data.get('max_neg_pnl_usdt'),
            max_neg_rsi=trade_data.get('max_neg_rsi')
        )

    def handle_cooldown_log(self, log_data):
        """Writes a new 'Closed' entry specifically for a cooldown event."""
        with self.db_lock:
            db_entry = {
                'Alert_id': self.get_next_alert_number(), 'Timestamp': self._format_datetime(datetime.now()),
                'Symbol': log_data['symbol'], 'Type': 'SHORT', 'Status': 'Closed',
                'Reason': log_data['reason'], 'Entry_RSI': log_data['rsi'],
                'Cooldown_Trigger_Value': log_data.get('pullback_percent'), 'Source': 'Bot'
            }
            self._write_df_to_db(pd.DataFrame([db_entry]), self.table_name, self.db_path)

    def handle_add_to_cooldown(self, data):
        """Adds or updates a coin in the cooldown database."""
        symbol, reason, end_time = data['symbol'], data['reason'], data['end_time']
        entry_time = time.time()
        
        with self.state.lock:
            self.state.cooldowned_coins[symbol] = {'reason': reason, 'end_time': end_time}

        query = f"""
        INSERT INTO {self.cooldown_table_name} (symbol, entry_date, exit_date, reason)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
        exit_date = excluded.exit_date,
        reason = excluded.reason;
        """
        params = (symbol, entry_time, end_time, reason)
        self._execute_query(query, params, db_path=self.cooldown_db_path)
        print(f"--- Added/Updated {symbol} in cooldown database. Reason: {reason} ---")

    def handle_remove_from_cooldown(self, data):
        """Removes a coin from the cooldown database."""
        symbol = data['symbol']
        with self.state.lock:
            if symbol in self.state.cooldowned_coins:
                del self.state.cooldowned_coins[symbol]
        
        query = f"DELETE FROM {self.cooldown_table_name} WHERE symbol = ?"
        self._execute_query(query, (symbol,), db_path=self.cooldown_db_path)
        print(f"--- Removed {symbol} from cooldown database. ---")

    def _write_df_to_db(self, df, table_name, db_path):
        """Appends a DataFrame to the database with thread safety."""
        with self.db_lock:
            conn = None
            try:
                conn = sqlite3.connect(db_path)
                df.to_sql(table_name, conn, if_exists='append', index=False)
            except sqlite3.Error as e:
                print(f"🚨 CRITICAL: Could not write to database! Error: {e}")
            finally:
                if conn: conn.close()

    def _update_trade_in_db(self, alert_num, new_status, reason, pnl_percent, pnl_usdt, close_price, exit_rsi, entry_time=None, max_neg_pnl_pct=None, max_neg_pnl_usdt=None, max_neg_rsi=None):
        """Updates a single trade record in the database."""
        with self.db_lock:
            exit_time_dt = datetime.now()
            exit_time_str = self._format_datetime(exit_time_dt)
            
            trade_duration_hours = None
            if entry_time:
                try:
                    duration_seconds = exit_time_dt.timestamp() - entry_time
                    trade_duration_hours = duration_seconds / 3600
                except (TypeError, ValueError) as e:
                    print(f"Warning: Could not calculate trade duration for Alert #{alert_num}. Error: {e}")
                    trade_duration_hours = None

            update_query = f"""
            UPDATE {self.table_name}
            SET Status = ?, Reason = ?, PNL_pct = ?, PNL_USDT = ?, Exit_Price = ?, Exit_RSI = ?,
                Exit_Time = ?, Trade_Duration_Hours = ?, max_neg_pnl_pct = ?, max_neg_pnl_usdt = ?, max_neg_rsi = ?
            WHERE Alert_id = ?
            """
            params = (
                new_status, reason, pnl_percent, pnl_usdt, close_price, exit_rsi,
                exit_time_str, trade_duration_hours, max_neg_pnl_pct, max_neg_pnl_usdt, max_neg_rsi,
                alert_num
            )
            self._execute_query(update_query, params)
            print(f"--- Updated trade Alert #{alert_num} to {new_status} in database. ---")

    def check_for_global_pause(self):
        """Checks recent trades and triggers a global pause if loss limit is hit."""
        with self.db_lock:
            try:
                df = self._read_db_to_df()
                if df.empty: return

                def parse_custom_date(date_str):
                    if not date_str or not isinstance(date_str, str): return None
                    try: return datetime.strptime(date_str, '%d-%m-%Y -> %I:%M:%S %p')
                    except ValueError:
                        try: return datetime.fromisoformat(date_str)
                        except (ValueError, TypeError): return None
                
                df['Timestamp_dt'] = df['Timestamp'].apply(parse_custom_date)
                
                twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
                recent_trades = df[df['Timestamp_dt'] > twenty_four_hours_ago]
                
                pnl_series = pd.to_numeric(recent_trades['PNL_USDT'], errors='coerce')
                loss_trades = pnl_series[pnl_series < 0]
                
                if len(loss_trades) >= config.LOSS_TRADES_LIMIT_24H:
                    self.state.activate_global_pause(len(loss_trades))
            except Exception as e:
                print(f"🚨 Error checking for global pause: {e}")

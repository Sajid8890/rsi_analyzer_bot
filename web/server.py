from flask import Flask, jsonify, render_template, request
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import os
import re
import shutil
import time
import config
import threading
import numpy as np
import json
import logging

def to_serializable(obj):
    """Converts NumPy, Pandas, and datetime types to JSON-safe types."""
    if isinstance(obj, (np.integer,)): return int(obj)
    elif isinstance(obj, (np.floating,)): return float(obj)
    elif isinstance(obj, (np.ndarray,)): return obj.tolist()
    elif hasattr(obj, "isoformat"): return obj.isoformat()
    elif isinstance(obj, (set, tuple)): return list(obj)
    return obj

def create_flask_app(state_manager, db_manager, websocket_service, trading_service):
    """Creates and configures the Flask application."""
    app = Flask(__name__, template_folder='templates')
    
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    def parse_custom_date(date_str):
        if not date_str or not isinstance(date_str, str): return None
        try: return datetime.strptime(date_str, '%d-%m-%Y -> %I:%M:%S %p')
        except ValueError:
            try: return datetime.fromisoformat(date_str)
            except (ValueError, TypeError): return None

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/data')
    def get_data():
        state = state_manager.get_full_state_snapshot()
        
        paper_unrealized_pnl = 0
        paper_trade_amount = 0
        live_trade_amount = 0

        for trade in state['active_trades'].values():
            if trade.get('source', 'Bot').lower() == 'live':
                live_trade_amount += trade.get('trade_amount', 0)
            else:
                paper_unrealized_pnl += trade.get('pnl_usdt', 0)
                paper_trade_amount += trade.get('trade_amount', 0)

        current_balance = state['portfolio']['balance']
        total_portfolio_value = current_balance + paper_trade_amount + paper_unrealized_pnl + live_trade_amount
        
        portfolio_data = {
            "balance": current_balance, "total_value": total_portfolio_value,
            "in_trades": paper_trade_amount + live_trade_amount, "available": current_balance,
        }

        stats_24h = {"profit_loss": 0.0, "trade_count": 0, "success_trades": 0, "failed_trades": 0}
        if os.path.exists(config.DATABASE_FILE):
            try:
                conn = sqlite3.connect(config.DATABASE_FILE)
                df = pd.read_sql_query("SELECT * FROM trades", conn)
                conn.close()

                if not df.empty:
                    df['Timestamp_dt'] = df['Timestamp'].apply(parse_custom_date)
                    twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
                    recent_trades = df[
                        (df['Timestamp_dt'] > twenty_four_hours_ago) & 
                        (df['Status'] == 'Closed') & (df['Source'] != 'Live')
                    ]
                    pnl_usdt_series = pd.to_numeric(recent_trades['PNL_USDT'], errors='coerce').fillna(0)
                    stats_24h['profit_loss'] = pnl_usdt_series.sum()
                    stats_24h['trade_count'] = len(recent_trades)
                    stats_24h['success_trades'] = len(recent_trades[pnl_usdt_series >= 0])
                    stats_24h['failed_trades'] = len(recent_trades[pnl_usdt_series < 0])
            except Exception as e:
                print(f"Warning: Could not calculate 24h stats. Error: {e}")
        
        market_data_list = []
        hot_coins_count = 0
        for symbol, data in state['coin_data'].items():
            status, pnl_percent, pnl_usdt, entry_price, status_reason, source, cooldown_end_time = "available", None, None, None, "", "Bot", None
            if symbol in state['active_trades']:
                trade = state['active_trades'][symbol]
                status = f"trading-{trade.get('source', 'Bot').lower()}"
                pnl_percent, pnl_usdt, entry_price = trade.get('pnl_percent'), trade.get('pnl_usdt'), trade.get('entry_price')
            elif symbol in state['alerted_coins']: # alerted_coins now comes from cooldowned_coins
                cooldown_info = state['alerted_coins'][symbol]
                if time.time() < cooldown_info.get('end_time', 0):
                    status = "cooldown"
                    status_reason, cooldown_end_time = cooldown_info.get('reason', ''), cooldown_info.get('end_time')
            
            if data.get('change_24h', 0) >= config.RSI_HOT_COIN_THRESHOLD:
                hot_coins_count += 1
            
            data.update({'status': status, 'pnl': pnl_percent, 'pnl_usdt': pnl_usdt, 'entry_price': entry_price, 'status_reason': status_reason, 'cooldown_end_time': cooldown_end_time})
            market_data_list.append({'symbol': symbol, **data})
        
        stats = {
            "total_coins": len(state['coin_data']), "rsi_monitoring": len(state['rsi_data']),
            "hot_coins": hot_coins_count, "open_trades": len(state['active_trades']),
            "cooldown_coins": len(state['alerted_coins']),
            "max_trades": config.MAX_OPEN_TRADES, "hot_coin_threshold": config.RSI_HOT_COIN_THRESHOLD,
            "global_stats": state['global_stats'], "stats_24h": stats_24h,
            "bot_uptime": str(timedelta(seconds=int((time.time() - state['bot_start_time']) + state['total_uptime_seconds'])))
        }
        
        data_response = {
            "market_data": market_data_list, "rsi_values": state['rsi_data'],
            "rsi_status": state['rsi_status'], "statistics": stats,
            "control_status": state['controls'], "portfolio": portfolio_data,
            "styles": state['styles'], "hide_cooldown_details": config.HIDE_COOLDOWN_DETAILS
        }

        safe_response = json.loads(json.dumps(data_response, default=to_serializable))
        return jsonify(safe_response)

    @app.route('/database')
    def get_database():
       with db_manager.db_lock:
            if not os.path.exists(config.DATABASE_FILE): return jsonify([])
            try:
                conn = sqlite3.connect(config.DATABASE_FILE)
                df = pd.read_sql_query("SELECT * FROM trades", conn)
                conn.close()
                df.rename(columns={
                    'Alert_id': 'Alert #', 'PNL_pct': 'PNL (%)', 'PNL_USDT': 'PNL (USDT)',
                    'Change_24h_pct': '24h Change %', 'Entry_Price': 'Entry Price',
                    'Exit_Price': 'Exit Price', 'Entry_RSI': 'Entry RSI', 'Exit_RSI': 'Exit RSI',
                    'Trade_Amount': 'Trade Amount', 'Leveraged_Amount': 'Leveraged Amount',
                    'Exit_Time': 'Exit Time', 'Trade_Duration_Hours': 'Duration (H)',
                    'max_neg_pnl_pct': 'Max Neg PNL %', 'max_neg_pnl_usdt': 'Max Neg PNL ($)',
                    'max_neg_rsi': 'Max Neg RSI'
                }, inplace=True)
                df = df.sort_values(by='Alert #', ascending=False).fillna('')
                for col in ['Entry Price', 'Exit Price', 'Max Neg PNL ($)']:
                    if col in df.columns: df[col] = df[col].apply(lambda x: f'{x:.8f}' if isinstance(x, (int, float)) and x != '' else x)
                for col in ['PNL (%)', 'Entry RSI', 'Exit RSI', '24h Change %', 'Max Neg PNL %', 'Max Neg RSI']:
                     if col in df.columns: df[col] = df[col].apply(lambda x: (f'{x:.2f}' if isinstance(x, (int, float)) else x) if x != '' else x)
                if 'Duration (H)' in df.columns:
                    df['Duration (H)'] = pd.to_numeric(df['Duration (H)'], errors='coerce').apply(lambda x: f'{x:.2f}' if pd.notna(x) else '')
                for col in ['PNL (USDT)', 'Trade Amount', 'Leveraged Amount']:
                    if col in df.columns: df[col] = df[col].apply(lambda x: f'{x:.4f}' if isinstance(x, (int, float)) and x != '' else x)
                return jsonify(df.to_dict('records'))
            except Exception as e: return jsonify({"error": str(e)}), 500

    # --- NEW ENDPOINT ---
    @app.route('/cooldown-database')
    def get_cooldown_database():
        with db_manager.db_lock:
            if not os.path.exists(config.COOLDOWN_DATABASE_FILE):
                return jsonify([])
            try:
                conn = sqlite3.connect(config.COOLDOWN_DATABASE_FILE)
                df = pd.read_sql_query("SELECT * FROM cooldowns ORDER BY exit_date DESC", conn)
                conn.close()
                
                df['entry_date'] = df['entry_date'].apply(lambda ts: datetime.fromtimestamp(ts).strftime('%d-%m-%Y -> %I:%M:%S %p'))
                df['exit_date'] = df['exit_date'].apply(lambda ts: datetime.fromtimestamp(ts).strftime('%d-%m-%Y -> %I:%M:%S %p'))
                
                df.rename(columns={
                    'symbol': 'Coin Name', 'entry_date': 'Entry Date', 
                    'exit_date': 'Expected Exit Date', 'reason': 'Reason of Cooldown'
                }, inplace=True)
                
                return jsonify(df.to_dict('records'))
            except Exception as e:
                return jsonify({"error": str(e)}), 500

    @app.route('/alerts')
    def get_alerts():
        with state_manager.lock:
            return jsonify(state_manager.alert_log[:20])

    @app.route('/toggle-control', methods=['POST'])
    def toggle_control():
        data = request.json
        control_name, action = data.get('control'), data.get('action')
        state_manager.toggle_control(control_name, action)
        return jsonify({"status": "success", "message": f"{control_name} action '{action}' processed."})

    @app.route('/manual-close/<symbol>', methods=['POST'])
    def manual_close(symbol):
        with state_manager.lock:
            if symbol not in state_manager.active_trades:
                return jsonify({"status": "error", "message": "Trade not found."}), 404
            trade_source = state_manager.active_trades[symbol].get('source', 'Bot').lower()
            if trade_source == 'live':
                print(f"--- UI instruction to close LIVE trade for {symbol}. Please close manually on Binance. ---")
                state_manager.close_trade(symbol, "Manual Close (Live)", 0, 0)
                return jsonify({"status": "success", "message": f"Live trade {symbol} marked as closed. Please verify on Binance."})
            current_price = state_manager.coin_data.get(symbol, {}).get('price', state_manager.active_trades[symbol]['entry_price'])
            current_rsi = state_manager.rsi_data.get(symbol, 0)
        state_manager.close_trade(symbol, "Manual Close", current_price, current_rsi)
        return jsonify({"status": "success", "message": f"Manual close initiated for {symbol}."})

    @app.route('/manual-trade', methods=['POST'])
    def manual_trade():
        data = request.json
        symbol, entry_price_str = data.get('symbol'), data.get('entry_price')
        with state_manager.lock:
            if symbol in state_manager.active_trades:
                return jsonify({"status": "error", "message": "Already in trade."}), 400
            try:
                entry_price = float(entry_price_str)
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid price."}), 400
            change_24h = state_manager.coin_data.get(symbol, {}).get('change_24h', 0)
            rsi_value = state_manager.rsi_data.get(symbol, 0)
        alert_number = db_manager.get_next_alert_number()
        if state_manager.open_trade(symbol, entry_price, rsi_value, change_24h, "Manual", alert_number):
            return jsonify({"status": "success", "message": "Manual trade opened."})
        else:
            return jsonify({"status": "error", "message": "Failed to open trade."}), 400

    @app.route('/discard-trade/<symbol>', methods=['POST'])
    def discard_trade(symbol):
        trade_data = None
        with state_manager.lock:
            if symbol in state_manager.active_trades:
                trade_data = state_manager.active_trades.pop(symbol)
                # --- MODIFIED: Publish event to add to cooldown ---
                state_manager.event_bus.publish('ADD_TO_COOLDOWN', {
                    'symbol': symbol, 'reason': 'Discarded',
                    'end_time': time.time() + config.DEFAULT_COOLDOWN_PERIOD
                })
                state_manager.add_alert_log(f"DISCARD: {symbol}", "")
        if trade_data:
            db_manager._update_trade_in_db(
                alert_num=trade_data['alert_num'], new_status="Closed", reason="Discarded (Master)", 
                pnl_percent="N/A", pnl_usdt="N/A", close_price="N/A", exit_rsi="N/A",
                entry_time=trade_data['entry_time']
            )
            return jsonify({"status": "success", "message": f"Trade for {symbol} has been discarded."})
        else:
            return jsonify({"status": "error", "message": "Trade not found."}), 404

    @app.route('/set-cooldown', methods=['POST'])
    def set_cooldown():
        data = request.json
        symbol, hours = data.get('symbol'), data.get('hours')
        if not symbol or not isinstance(hours, (int, float)) or hours <= 0:
            return jsonify({"status": "error", "message": "Invalid symbol or duration."}), 400
        # --- MODIFIED: Publish event to add to cooldown ---
        state_manager.event_bus.publish('ADD_TO_COOLDOWN', {
            'symbol': symbol, 'reason': 'Manual Add',
            'end_time': time.time() + (hours * 3600)
        })
        return jsonify({"status": "success", "message": f"{symbol} is on cooldown for {hours} hours."})

    @app.route('/remove-cooldown/<symbol>', methods=['POST'])
    def remove_cooldown(symbol):
        # --- MODIFIED: Publish event to remove from cooldown ---
        state_manager.event_bus.publish('REMOVE_FROM_COOLDOWN', {'symbol': symbol})
        return jsonify({"status": "success", "message": f"Cooldown for {symbol} has been removed."})

    @app.route('/get-config')
    def get_config():
        current_config = {}
        for var in dir(config):
            if var.isupper():
                current_config[var] = getattr(config, var)
        with state_manager.lock:
            current_config['portfolio_balance'] = state_manager.portfolio['balance']
            current_config.update(state_manager.styles)
        return jsonify(current_config)

    @app.route('/update-config', methods=['POST'])
    def update_config():
        new_config = request.json
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config.py')
            with open(config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            config_vars_to_update = [var for var in dir(config) if var.isupper()]
            for i, line in enumerate(lines):
                for var, value in new_config.items():
                    if var in config_vars_to_update and re.match(rf"^{var}\s*=\s*.*", line):
                        if hasattr(config, var):
                            original_type = type(getattr(config, var))
                            try:
                                if original_type == bool:
                                    live_value = str(value).lower() in ['true', '1', 't', 'y', 'yes']
                                else:
                                    live_value = original_type(value)
                                setattr(config, var, live_value)
                            except (ValueError, TypeError):
                                print(f"Warning: Could not live-update '{var}'.")
                        if isinstance(value, str) and not value.replace('.', '', 1).isdigit():
                             if isinstance(getattr(config, var, None), bool):
                                 lines[i] = f'{var} = {str(value).title()}\n'
                             else:
                                 lines[i] = f'{var} = "{value}"\n'
                        else:
                             lines[i] = f'{var} = {value}\n'
                        break
            with open(config_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            with state_manager.lock:
                if 'portfolio_balance' in new_config:
                    state_manager.portfolio['balance'] = float(new_config['portfolio_balance'])
                    state_manager.event_bus.publish('PORTFOLIO_UPDATED')
                style_updated = False
                for key in config.DEFAULT_STYLES:
                    if key in new_config and state_manager.styles[key] != new_config[key]:
                        state_manager.styles[key] = new_config[key]
                        style_updated = True
                if style_updated:
                    state_manager.event_bus.publish('STYLES_UPDATED')
            return jsonify({"status": "success", "message": "Configuration updated and saved to file."})
        except Exception as e:
            print(f"🚨 CRITICAL: Failed to write config to file! Error: {e}")
            return jsonify({"status": "error", "message": f"Failed to write to file: {e}"}), 500

    @app.route('/master-reset', methods=['POST'])
    def master_reset():
        action = request.json.get('action')
        msg = "Invalid action."
        if action == 'close_all_trades':
            with state_manager.lock:
                symbols_to_close = list(state_manager.active_trades.keys())
            for symbol in symbols_to_close:
                with state_manager.lock:
                    trade = state_manager.active_trades.get(symbol)
                    if not trade: continue
                    if trade.get('source', 'Bot').lower() == 'live':
                        trading_service.binance_trader.close_live_trade(symbol)
                        state_manager.close_trade(symbol, "Master Close All", 0, 0)
                    else:
                        current_price = state_manager.coin_data.get(symbol, {}).get('price', trade['entry_price'])
                        current_rsi = state_manager.rsi_data.get(symbol, 0)
                        state_manager.close_trade(symbol, "Master Close All", current_price, current_rsi)
            msg = f"Initiated closing for all {len(symbols_to_close)} trades."
        elif action == 'discard_trades':
            with state_manager.lock:
                num_trades = len(state_manager.active_trades)
                for symbol in list(state_manager.active_trades.keys()):
                    trade_data = state_manager.active_trades.pop(symbol)
                    db_manager._update_trade_in_db(trade_data['alert_num'], "Closed", "Discarded (Master)", "N/A", "N/A", "N/A", "N/A", trade_data['entry_time'])
            msg = f"Successfully discarded all {num_trades} open trades."
        elif action == 'remove_cooldowns':
            with state_manager.lock:
                num_cooldowns = len(state_manager.cooldowned_coins)
                # --- MODIFIED: Publish event for each coin to be removed ---
                for symbol in list(state_manager.cooldowned_coins.keys()):
                    state_manager.event_bus.publish('REMOVE_FROM_COOLDOWN', {'symbol': symbol})
            msg = f"Successfully removed all {num_cooldowns} cooldowns."
        elif action == 'reset_global_stats':
            with state_manager.lock:
                state_manager.global_stats.update({"global_profit_usdt": 0.0, "global_loss_usdt": 0.0, "profitable_trades": 0, "loss_trades": 0})
                state_manager.event_bus.publish('STATS_UPDATED')
            msg = "Global stats have been reset to zero."
        elif action == 'reset_database':
            with db_manager.db_lock:
                if os.path.exists(config.DATABASE_FILE):
                    os.remove(config.DATABASE_FILE)
                if os.path.exists(config.COOLDOWN_DATABASE_FILE):
                    os.remove(config.COOLDOWN_DATABASE_FILE)
                msg = "Database files have been deleted."
        return jsonify({"status": "success", "message": msg})

    @app.route('/refresh-coin-list', methods=['POST'])
    def refresh_coin_list_endpoint():
        if not websocket_service:
            return jsonify({"status": "error", "message": "WebSocket service not available."}), 500
        try:
            threading.Thread(target=websocket_service.fetch_listing_times).start()
            return jsonify({"status": "success", "message": "Refresh initiated."})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/refresh-balance', methods=['POST'])
    def refresh_balance_endpoint():
        if not trading_service:
            return jsonify({"status": "error", "message": "Trading service not available."}), 500
        try:
            # threading.Thread(target=trading_service.update_portfolio_balance).start()
            return jsonify({"status": "success", "message": "Balance refresh initiated."})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/shutdown-bot', methods=['POST'])
    def shutdown_bot():
        print("--- !!! Received shutdown command from UI. Exiting... !!! ---")
        os._exit(0)
        return jsonify({"status": "success", "message": "Shutdown command sent."})

    return app

import json
import os
from datetime import timedelta
import time
import config

class FileManager:
    """Handles loading and saving of JSON-based state files."""
    def __init__(self, state_manager, event_bus):
        self.state = state_manager
        self.event_bus = event_bus
        self._subscribe_to_events()

    def _subscribe_to_events(self):
        self.event_bus.subscribe('PORTFOLIO_UPDATED', lambda d: self.save_portfolio())
        self.event_bus.subscribe('STATS_UPDATED', lambda d: self.save_global_stats())
        self.event_bus.subscribe('STYLES_UPDATED', lambda d: self.save_styles())
        self.event_bus.subscribe('STATE_UPDATED_RSI', lambda d: self.save_rsi_peak_tracker())

    def load_all(self):
        """Load all persistent data from files at startup."""
        self.load_styles()
        self.load_portfolio()
        self.load_global_stats()
        self.load_uptime()
        self.load_rsi_peak_tracker()

    def save_all_on_exit(self):
        """Save all necessary data on bot shutdown."""
        self.save_uptime()
        self.save_global_stats()
        self.save_portfolio()
        self.save_styles()
        self.save_rsi_peak_tracker()

    def load_rsi_peak_tracker(self):
        if os.path.exists(config.RSI_PEAK_TRACKER_FILE):
            try:
                with open(config.RSI_PEAK_TRACKER_FILE, 'r') as f:
                    self.state.rsi_peak_tracker = json.load(f)
                print("--- RSI peak tracker history loaded. ---")
            except (IOError, json.JSONDecodeError) as e:
                print(f"🚨 Warning: Could not load RSI peak tracker file. Starting fresh. Error: {e}")

    def save_rsi_peak_tracker(self):
        try:
            with open(config.RSI_PEAK_TRACKER_FILE, 'w') as f:
                json.dump(self.state.rsi_peak_tracker, f, indent=4)
        except IOError as e:
            print(f"🚨 CRITICAL: Could not save RSI peak tracker! Error: {e}")

    def load_styles(self):
        if os.path.exists(config.STYLE_CONFIG_FILE):
            try:
                with open(config.STYLE_CONFIG_FILE, 'r') as f:
                    styles = json.load(f)
                for key, value in config.DEFAULT_STYLES.items():
                    if key not in styles:
                        styles[key] = value
                self.state.styles = styles
            except (IOError, json.JSONDecodeError) as e:
                print(f"🚨 Warning: Could not load styles file. Using default. Error: {e}")
        else:
            self.save_styles()

    def save_styles(self):
        try:
            with open(config.STYLE_CONFIG_FILE, 'w') as f:
                json.dump(self.state.styles, f, indent=4)
        except IOError as e:
            print(f"🚨 CRITICAL: Could not save styles! Error: {e}")

    def load_portfolio(self):
        if os.path.exists(config.PORTFOLIO_FILE):
            try:
                with open(config.PORTFOLIO_FILE, 'r') as f:
                    self.state.portfolio = json.load(f)
            except (IOError, json.JSONDecodeError) as e:
                print(f"🚨 Warning: Could not load portfolio file. Using default. Error: {e}")
        else:
            self.save_portfolio()

    def save_portfolio(self):
        try:
            with open(config.PORTFOLIO_FILE, 'w') as f:
                json.dump(self.state.portfolio, f, indent=4)
        except IOError as e:
            print(f"🚨 CRITICAL: Could not save portfolio! Error: {e}")

    def load_global_stats(self):
        if os.path.exists(config.STATS_FILE):
            try:
                with open(config.STATS_FILE, 'r') as f:
                    stats_from_file = json.load(f)
                    self.state.global_stats.update(stats_from_file)
                print("--- Global stats loaded. ---")
            except (IOError, json.JSONDecodeError) as e:
                print(f"🚨 Warning: Could not load global stats file. Starting fresh. Error: {e}")

    def save_global_stats(self):
        try:
            with open(config.STATS_FILE, 'w') as f:
                json.dump(self.state.global_stats, f, indent=4)
        except IOError as e:
            print(f"🚨 CRITICAL: Could not save global stats! Error: {e}")

    def load_uptime(self):
        if os.path.exists(config.UPTIME_FILE):
            try:
                with open(config.UPTIME_FILE, 'r') as f:
                    data = json.load(f)
                    self.state.total_uptime_seconds = data.get("total_uptime_seconds", 0)
            except (IOError, json.JSONDecodeError):
                pass

    def save_uptime(self):
        current_session_uptime = time.time() - self.state.bot_start_time
        final_total_uptime = self.state.total_uptime_seconds + current_session_uptime
        try:
            with open(config.UPTIME_FILE, 'w') as f:
                json.dump({"total_uptime_seconds": final_total_uptime}, f)
            print(f"--- Total uptime saved: {timedelta(seconds=int(final_total_uptime))}. ---")
        except IOError as e:
            print(f"🚨 CRITICAL: Could not save uptime! Error: {e}")

import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import config
from .base_service import BaseService
from plyer import notification
import winsound



import tkinter as tk
import winsound
import threading
from datetime import datetime

BEEP_ENABLED = True  # Toggle sound


def show_trade_popup(title, entry_price, entry_rsi):
    def popup():
        root = tk.Tk()
        root.title(title)
        root.geometry("360x240")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = tk.Frame(root, bg="#ffffff")
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Title
        tk.Label(
            frame, text=title, fg="#000000", bg="#ffffff",
            font=("Segoe UI", 15, "bold")
        ).pack(pady=(0, 8))

        # Time
        alert_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tk.Label(
            frame, text=f"Time: {alert_time}",
            fg="#444444", bg="#ffffff",
            font=("Segoe UI", 10)
        ).pack()

        # TABLE
        table = tk.Frame(frame, bg="#ffffff")
        table.pack(pady=10)

        # Entry Price row
        tk.Label(
            table, text="Entry Price:", bg="#ffffff",
            fg="#000000", font=("Segoe UI", 11, "bold")
        ).grid(row=0, column=0, sticky="w", pady=3)

        entry_label = tk.Label(
            table, text=f"${entry_price:.5f}",
            bg="#ffffff", fg="#0b8a16",
            font=("Segoe UI", 11, "bold")
        )
        entry_label.grid(row=0, column=1, sticky="w", pady=3)

        # RSI row
        tk.Label(
            table, text="Entry RSI:", bg="#ffffff",
            fg="#000000", font=("Segoe UI", 11, "bold")
        ).grid(row=1, column=0, sticky="w", pady=3)

        tk.Label(
            table, text=f"{entry_rsi:.2f}",
            bg="#ffffff", fg="#000000",
            font=("Segoe UI", 11)
        ).grid(row=1, column=1, sticky="w", pady=3)

        # Copy only the numeric entry price (NO popup alert)
        def copy_price():
            clean_price = f"{entry_price:.5f}"
            root.clipboard_clear()
            root.clipboard_append(clean_price)
            # No alert shown — silent copy

        tk.Button(
            frame, text="Copy Entry Price", command=copy_price,
            bg="#1a73e8", fg="white", font=("Segoe UI", 10, "bold"),
            padx=12, pady=6, relief="flat", cursor="hand2"
        ).pack(pady=(0, 12))

        # Clean & Bigger Close Button
        tk.Button(
            frame, text="Close", command=root.destroy,
            bg="#eeeeee", fg="#000000",
            font=("Segoe UI", 11, "bold"),
            width=20, height=1,
            relief="flat", cursor="hand2"
        ).pack()

        root.mainloop()

    threading.Thread(target=popup).start()



class EmailService(BaseService):
    """
    Handles sending email notifications based on bot events.
    """
    def __init__(self, state_manager, event_bus):
        super().__init__(state_manager, event_bus)
        self._subscribe_to_events()

    def _subscribe_to_events(self):
        """Subscribe to events that should trigger an email."""
        self.event_bus.subscribe('TRADE_OPENED', self.handle_trade_opened)
        self.event_bus.subscribe('TRADE_CLOSED', self.handle_trade_closed)
        self.event_bus.subscribe('GLOBAL_PAUSE_TRIGGERED', self.handle_global_pause)

    def run(self):
        """The EmailService is event-driven, so its run loop is simple."""
        pass

    # def handle_trade_opened(self, trade_data):
    #     if not self.state.controls['email_enabled'].is_set(): return
        
    #     symbol = next((s for s, t in self.state.active_trades.items() if t['alert_num'] == trade_data['alert_num']), None)
    #     if not symbol: return

    #     subject = f"📉 {trade_data['source'].upper()} SHORT: {symbol} @ ${trade_data['entry_price']:.5f}"
    #     leveraged_amount = trade_data['trade_amount'] * trade_data['leverage']
        
    #     html_body = f"""
    #     <h2>New {trade_data['source'].upper()} SHORT Trade: {symbol}</h2>
    #     <p><b>Entry Price:</b> ${trade_data['entry_price']:.5f}</p>
    #     <p><b>Entry RSI:</b> {trade_data['entry_rsi']:.2f}</p>
    #     <p><b>Trade Amount:</b> ${trade_data['trade_amount']:.2f} (Leveraged: ${leveraged_amount:.2f})</p>
    #     """
    #     self._send_email_with_retries(subject, html_body)


    def handle_trade_opened(self, trade_data):

        symbol = next((s for s, t in self.state.active_trades.items()
                    if t['alert_num'] == trade_data['alert_num']), None)
        if not symbol:
            return

        title = f"{trade_data['source'].upper()} SHORT: {symbol}"

        entry_price = trade_data['entry_price']
        entry_rsi = trade_data['entry_rsi']

        if BEEP_ENABLED:
            winsound.Beep(900, 500)

        show_trade_popup(title, entry_price, entry_rsi)







    def handle_trade_closed(self, close_data):
        if not self.state.controls['email_enabled'].is_set(): return

        trade_data = close_data['trade_data']
        pnl_usdt = trade_data['pnl_usdt']
        pnl_percent = trade_data['pnl_percent']
        
        symbol = "UNKNOWN"
        for s, info in self.state.alerted_coins.items():
            if info.get('reason') == close_data['reason']:
                symbol = s
                break

        subject = f"✅ SHORT TRADE CLOSED: {symbol} - {close_data['reason']} ({pnl_percent:+.2f}%)"
        html_body = f"""
        <h2>SHORT Trade Closed: {symbol}</h2>
        <p><b>Reason:</b> {close_data['reason']}</p>
        <p><b>P/L:</b> <b style="color: {'green' if pnl_usdt >= 0 else 'red'};">{pnl_percent:.2f}% (${pnl_usdt:.4f})</b></p>
        <p><b>Entry Price:</b> ${trade_data['entry_price']:.8f} (RSI: {trade_data.get('entry_rsi', 'N/A'):.2f})</p>
        <p><b>Close Price:</b> ${close_data['close_price']:.8f} (RSI: {close_data['exit_rsi']:.2f})</p>
        <p><b>New Portfolio Balance:</b> ${close_data['new_balance']:.2f}</p>
        """
        self._send_email_with_retries(subject, html_body)

    def handle_global_pause(self, data):
        if not self.state.controls['email_enabled'].is_set(): return
        
        subject = f"🚨 BOT PAUSED: {data['loss_count']} Losses in 24 Hours"
        body = f"""
        <h2>Trading has been automatically paused.</h2>
        <p>The bot has recorded {data['loss_count']} losing trades in the last 24 hours, exceeding the limit of {config.LOSS_TRADES_LIMIT_24H}.</p>
        <p>Trading will be paused for {config.GLOBAL_PAUSE_DURATION_HOURS} hours or until manually resumed.</p>
        """
        self._send_email_with_retries(subject, body)

    def _send_email_with_retries(self, subject, html_body, max_retries=3):
        """
        The actual email sending logic, now wrapped in a retry loop.
        """
        for attempt in range(max_retries):
            try:
                msg = MIMEMultipart()
                msg['From'] = config.EMAIL_SENDER
                msg['To'] = config.EMAIL_RECEIVER
                msg['Subject'] = subject
                msg.attach(MIMEText(html_body, 'html'))

                with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
                    server.starttls()
                    server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
                    server.send_message(msg)
                
                print(f"--- Email sent successfully: '{subject}' ---")
                return True

            except smtplib.SMTPAuthenticationError as e:
                print(f"🚨 CRITICAL EMAIL ERROR: SMTP Authentication failed. Check EMAIL_SENDER and EMAIL_PASSWORD in config. Error: {e}")
                return False
            except Exception as e:
                print(f"🚨 EMAIL ERROR: Failed to send email on attempt {attempt + 1}/{max_retries}. Error: {e}")
                if attempt < max_retries - 1:
                    delay = 10 * (2 ** attempt)
                    print(f"--- Retrying email in {delay} seconds... ---")
                    time.sleep(delay)
                else:
                    print(f"🚨 CRITICAL EMAIL ERROR: All {max_retries} attempts to send email failed.")
                    return False
        return False

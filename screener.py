import yfinance as yf
import pandas as pd
import smtplib
import requests
from io import StringIO
from email.mime.text import MIMEText
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- CONFIGURATION ---
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# Validate credentials
if not all([EMAIL_ADDRESS, EMAIL_PASSWORD, PHONE_NUMBER]):
    print("WARNING: Missing environment variables. SMS will fail.")

TARGET_PHONE = f"{PHONE_NUMBER}@vtext.com"

def get_sp500_tickers():
    """Scrapes S&P 500 tickers from Wikipedia using a fake User-Agent."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        table = pd.read_html(StringIO(response.text))
        df = table[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        return tickers
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

def run_screener():
    print("Fetching S&P 500 tickers...")
    tickers = get_sp500_tickers()
    
    if not tickers:
        print("No tickers found. Exiting.")
        return [], []

    print(f"Downloading data for {len(tickers)} stocks...")
    
    try:
        # 2y is perfect for accurate RSI convergence
        data = yf.download(tickers, period="2y", group_by='ticker', progress=False)
    except Exception as e:
        print(f"YFinance Download Error: {e}")
        return [], []
    
    bullish_candidates = []
    bearish_candidates = []
    
    print("Processing indicators...")
    for ticker in tickers:
        try:
            if ticker not in data or data[ticker].empty:
                continue
            
            df = data[ticker].copy()
            if len(df) < 200: # Need 200 days for accurate 50 SMA + RSI warmup
                continue

            # --- SAFE DATA EXTRACTION ---
            # Handles both Series (multi-index) and Scalar values safely
            try:
                curr_close = df['Close'].iloc[-1]
                curr_vol = df['Volume'].iloc[-1]
                
                # Convert to float if it's a Series, otherwise cast directly
                current_price = float(curr_close.iloc[0]) if isinstance(curr_close, pd.Series) else float(curr_close)
                current_volume = float(curr_vol.iloc[0]) if isinstance(curr_vol, pd.Series) else float(curr_vol)
            except Exception:
                continue # Skip malformed data
            
            # --- INDICATORS ---
            
            # SMAs (Extract as floats)
            sma20_series = df['Close'].rolling(window=20).mean().iloc[-1]
            sma50_series = df['Close'].rolling(window=50).mean().iloc[-1]
            
            sma20 = float(sma20_series.iloc[0]) if isinstance(sma20_series, pd.Series) else float(sma20_series)
            sma50 = float(sma50_series.iloc[0]) if isinstance(sma50_series, pd.Series) else float(sma50_series)

            # --- CORRECT RSI CALCULATION (Wilder's Smoothing) ---
            delta = df['Close'].diff()
            
            # Separate gains and losses
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            # USE EXPONENTIAL MOVING AVERAGE (Matches Finviz/TradingView)
            # com=13 is equivalent to alpha=1/14, the standard Wilder's smoothing
            avg_gain = gain.ewm(com=13, adjust=False).mean()
            avg_loss = loss.ewm(com=13, adjust=False).mean()
            
            rs = avg_gain / avg_loss
            rsi_series = 100 - (100 / (1 + rs))
            
            # Extract final RSI value safely
            current_rsi_val = rsi_series.iloc[-1]
            current_rsi = float(current_rsi_val.iloc[0]) if isinstance(current_rsi_val, pd.Series) else float(current_rsi_val)

            # --- LOGIC WITH BUFFER ---
            
            # Bullish: RSI > 40, Price < SMA20 (Dip), Price > SMA50 (Uptrend)
            # CHANGE: Added * 1.01 to allow stocks that are 1% ABOVE the SMA20 (near misses)
            if (current_rsi > 40) and (current_price < sma20 * 1.01) and (current_price > sma50):
                bullish_candidates.append({
                    'Ticker': ticker,
                    'Price': current_price,
                    'Vol': current_volume,
                    'RSI': current_rsi,
                    'Setup': 'Bull'
                })

            # Bearish: RSI < 60, Price > SMA20 (Rally), Price < SMA50 (Downtrend)
            # CHANGE: Added * 0.99 to allow stocks that are 1% BELOW the SMA20
            if (current_rsi < 60) and (current_price > sma20 * 0.99) and (current_price < sma50):
                bearish_candidates.append({
                    'Ticker': ticker,
                    'Price': current_price,
                    'Vol': current_volume,
                    'RSI': current_rsi,
                    'Setup': 'Bear'
                })
                
        except Exception:
            continue

    # Sort by Volume
    bullish_candidates.sort(key=lambda x: x['Vol'], reverse=True)
    bearish_candidates.sort(key=lambda x: x['Vol'], reverse=True)
    
    # Return top 20 since we expect more hits now
    return bullish_candidates[:20], bearish_candidates[:20]

def send_sms(bulls, bears):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("Skipping SMS: No credentials found.")
        return

    msg_body = f"STOCKS REPORT ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    
    msg_body += "--- CALLS (Dip Buy) ---\n"
    if not bulls: msg_body += "None found.\n"
    for s in bulls:
        msg_body += f"{s['Ticker']} ${s['Price']:.2f} (RSI: {s['RSI']:.0f})\n"
        
    msg_body += "\n--- PUTS (Reject) ---\n"
    if not bears: msg_body += "None found.\n"
    for s in bears:
        msg_body += f"{s['Ticker']} ${s['Price']:.2f} (RSI: {s['RSI']:.0f})\n"

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        
        msg = MIMEText(msg_body)
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = TARGET_PHONE
        msg['Subject'] = "Daily Screener"
        
        server.sendmail(EMAIL_ADDRESS, TARGET_PHONE, msg.as_string())
        server.quit()
        print(f"SMS sent successfully to {TARGET_PHONE}")
    except Exception as e:
        print(f"Failed to send SMS: {e}")

if __name__ == "__main__":
    bulls, bears = run_screener()
    send_sms(bulls, bears)
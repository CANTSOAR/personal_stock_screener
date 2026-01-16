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
    # We print a warning instead of crashing, so we can see logs if needed
    print("WARNING: Missing environment variables. SMS will fail.")

TARGET_PHONE = f"{PHONE_NUMBER}@vtext.com"

def get_sp500_tickers():
    """Scrapes S&P 500 tickers from Wikipedia using a fake User-Agent."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    
    # This header makes Wikipedia think you are a Chrome Browser on Windows
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Check for 403/404 errors
        
        # Use StringIO to wrap the text so pandas can read it
        table = pd.read_html(StringIO(response.text))
        df = table[0]
        
        # Wiki uses dots (BRK.B) but Yahoo uses dashes (BRK-B)
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
    
    # Download data
    try:
        data = yf.download(tickers, period="3mo", group_by='ticker', progress=False)
    except Exception as e:
        print(f"YFinance Download Error: {e}")
        return [], []
    
    bullish_candidates = []
    bearish_candidates = []
    
    print("Processing indicators...")
    for ticker in tickers:
        try:
            # Handle cases where ticker data is missing
            if ticker not in data or data[ticker].empty:
                continue
            
            df = data[ticker].copy()
            if len(df) < 50:
                continue

            # Handle MultiIndex vs Single Index (yfinance versions vary)
            try:
                # Try getting scalar values
                current_price = float(df['Close'].iloc[-1])
                current_volume = float(df['Volume'].iloc[-1])
            except:
                # Fallback for Series extraction
                current_price = df['Close'].iloc[-1]
                current_volume = df['Volume'].iloc[-1]
                # Ensure they are floats not Series
                if isinstance(current_price, pd.Series): current_price = float(current_price.iloc[0])
                if isinstance(current_volume, pd.Series): current_volume = float(current_volume.iloc[0])
            
            # Rule: Price under $50
            if current_price > 50:
                continue
                
            # SMAs
            sma20 = df['Close'].rolling(window=20).mean().iloc[-1]
            sma50 = df['Close'].rolling(window=50).mean().iloc[-1]
            
            # Extract scalar if they are series
            if isinstance(sma20, pd.Series): sma20 = float(sma20.iloc[0])
            if isinstance(sma50, pd.Series): sma50 = float(sma50.iloc[0])

            # RSI Calculation
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1]
            if isinstance(current_rsi, pd.Series): current_rsi = float(current_rsi.iloc[0])

            # --- BULLISH LOGIC ---
            # RSI > 40, Price < SMA20 (Dip), Price > SMA50 (Uptrend)
            if (current_rsi > 40) and (current_price < sma20) and (current_price > sma50):
                bullish_candidates.append({
                    'Ticker': ticker,
                    'Price': current_price,
                    'Vol': current_volume,
                    'RSI': current_rsi,
                    'Setup': 'Bull'
                })

            # --- BEARISH LOGIC ---
            # RSI < 60, Price > SMA20 (Rally), Price < SMA50 (Downtrend)
            if (current_rsi < 60) and (current_price > sma20) and (current_price < sma50):
                bearish_candidates.append({
                    'Ticker': ticker,
                    'Price': current_price,
                    'Vol': current_volume,
                    'RSI': current_rsi,
                    'Setup': 'Bear'
                })
                
        except Exception:
            continue

    # Sort and slice
    bullish_candidates.sort(key=lambda x: x['Vol'], reverse=True)
    bearish_candidates.sort(key=lambda x: x['Vol'], reverse=True)
    
    return bullish_candidates[:10], bearish_candidates[:10]

def send_sms(bulls, bears):
    # If no credentials, just print and exit
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("Skipping SMS: No credentials found.")
        print(f"Bulls found: {len(bulls)}")
        print(f"Bears found: {len(bears)}")
        return

    msg_body = f"STOCKS REPORT ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    
    msg_body += "--- CALLS (Dip Buy) ---\n"
    if not bulls: msg_body += "None found.\n"
    for s in bulls:
        msg_body += f"{s['Ticker']} ${s['Price']:.2f} (Vol: {s['Vol']/1000:.0f}K)\n"
        
    msg_body += "\n--- PUTS (Reject) ---\n"
    if not bears: msg_body += "None found.\n"
    for s in bears:
        msg_body += f"{s['Ticker']} ${s['Price']:.2f} (Vol: {s['Vol']/1000:.0f}K)\n"

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
        print(EMAIL_ADDRESS, len(EMAIL_PASSWORD), EMAIL_PASSWORD[0], PHONE_NUMBER[3:6])
        print(f"Failed to send SMS: {e}")

if __name__ == "__main__":
    bulls, bears = run_screener()
    send_sms(bulls, bears)
import yfinance as yf
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables from a .env file (if it exists)
load_dotenv()

# --- CONFIGURATION ---
# Access variables using os.getenv
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# Validate credentials exist before running
if not all([EMAIL_ADDRESS, EMAIL_PASSWORD, PHONE_NUMBER]):
    raise ValueError("Missing environment variables! Check your .env file or GitHub Secrets.")

# Verizon format: number@vtext.com
TARGET_PHONE = f"{PHONE_NUMBER}@vtext.com"

def get_sp500_tickers():
    """Scrapes S&P 500 tickers from Wikipedia."""
    try:
        # Use lxml for better table parsing
        table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
        df = table[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        return tickers
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

def run_screener():
    print("Fetching S&P 500 tickers...")
    tickers = get_sp500_tickers()
    
    print(f"Downloading data for {len(tickers)} stocks...")
    # 'threads' is deprecated in new yfinance versions, removed for compatibility
    data = yf.download(tickers, period="3mo", group_by='ticker', progress=False)
    
    bullish_candidates = []
    bearish_candidates = []
    
    print("Processing indicators...")
    for ticker in tickers:
        try:
            # Handle empty data
            if ticker not in data or data[ticker].empty:
                continue
            
            df = data[ticker].copy()
            if len(df) < 50:
                continue

            # Handle MultiIndex columns (Fix for newer yfinance versions)
            try:
                current_price = df['Close'].iloc[-1].item() # Convert to float
                current_volume = df['Volume'].iloc[-1].item()
            except:
                # Fallback for single index
                current_price = df['Close'].iloc[-1]
                current_volume = df['Volume'].iloc[-1]
            
            if current_price > 50:
                continue
                
            # SMAs
            sma20 = df['Close'].rolling(window=20).mean().iloc[-1]
            sma50 = df['Close'].rolling(window=50).mean().iloc[-1]
            
            # RSI
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1]

            # Bullish Logic
            if (current_rsi > 40) and (current_price < sma20) and (current_price > sma50):
                bullish_candidates.append({
                    'Ticker': ticker,
                    'Price': current_price,
                    'Vol': current_volume,
                    'RSI': current_rsi,
                    'Setup': 'Bull'
                })

            # Bearish Logic
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

    bullish_candidates.sort(key=lambda x: x['Vol'], reverse=True)
    bearish_candidates.sort(key=lambda x: x['Vol'], reverse=True)
    
    return bullish_candidates[:10], bearish_candidates[:10]

def send_sms(bulls, bears):
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
        print(f"Failed to send SMS: {e}")

if __name__ == "__main__":
    bulls, bears = run_screener()
    send_sms(bulls, bears)
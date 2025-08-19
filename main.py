#!/usr/bin/env python3

import json
import os
import sys
import requests
import csv
from datetime import datetime, timedelta
import pandas as pd

def save_json(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def get_alpha_vantage_price(symbol, api_key):
    url = f"https://www.alphavantage.co/query"
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
        "apikey": api_key
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if "Global Quote" in data and "05. price" in data["Global Quote"]:
            price = float(data["Global Quote"]["05. price"])
            return price
        elif "Error Message" in data:
            print(f"API Error for {symbol}: {data['Error Message']}")
            return None
        elif "Note" in data:
            print(f"API Limit for {symbol}: {data['Note']}")
            return None
        else:
            print(f"Unexpected response for {symbol}: {data}")
            # Try alternative ticker formats
            if symbol == "MYOMO":
                # Try MYO ticker
                params["symbol"] = "MYO"
                response = requests.get(url, params=params)
                data = response.json()
                if "Global Quote" in data and "05. price" in data["Global Quote"]:
                    price = float(data["Global Quote"]["05. price"])
                    print(f"Found {symbol} as MYO: ${price:.4f}")
                    return price
            return None
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None

def calculate_daily_changes(current_data, previous_data):
    if not previous_data:
        return None
    
    individual_changes = {}
    for symbol in current_data["prices"]:
        if symbol in previous_data.get("prices", {}):
            current_price = current_data["prices"][symbol]
            prev_price = previous_data["prices"][symbol]
            current_qty = current_data["quantities"][symbol]
            
            price_change = current_price - prev_price
            price_change_pct = (price_change / prev_price) * 100 if prev_price > 0 else 0
            value_change = price_change * current_qty
            
            individual_changes[symbol] = {
                "price_change": price_change,
                "price_change_pct": price_change_pct,
                "value_change": value_change
            }
    
    current_total = float(current_data["total_value"])
    prev_total = float(previous_data.get("total_value", current_total))
    
    total_change = current_total - prev_total
    total_change_pct = (total_change / prev_total) * 100 if prev_total > 0 else 0
    
    return {
        "individual": individual_changes,
        "portfolio": {
            "total_change": total_change,
            "total_change_pct": total_change_pct
        }
    }

def get_previous_day_data():
    try:
        if not os.path.exists("data/portfolio_history.csv"):
            print("No portfolio history CSV found")
            return None
            
        df = pd.read_csv("data/portfolio_history.csv")
        if len(df) < 1:
            print("Portfolio history CSV is empty")
            return None
            
        if len(df) >= 2:
            # Get second to last row (previous day)
            prev_row = df.iloc[-2]
        else:
            # If only one row, use it as baseline
            prev_row = df.iloc[-1]
        
        symbols = ["GEVO", "FEIM", "ARQ", "UPXI", "SERV", "MYOMO", "CABA"]
        prices = {}
        quantities = {}
        
        for symbol in symbols:
            price_col = f"{symbol}_price"
            qty_col = f"{symbol}_qty"
            
            # Check if columns exist
            if price_col in prev_row and qty_col in prev_row:
                if pd.notna(prev_row[price_col]) and prev_row[qty_col] > 0:
                    prices[symbol] = prev_row[price_col]
                    quantities[symbol] = prev_row[qty_col]
        
        return {
            "prices": prices,
            "quantities": quantities,
            "total_value": str(prev_row["total_value"]) if "total_value" in prev_row else "0"
        }
        
    except Exception as e:
        print(f"Error loading previous day data: {e}")
        print("Continuing without daily change calculation...")
        return None

def execute_trading_decisions(holdings, prices, date, cash):
    claude_actions = []
    
    try:
        with open("trading_decisions.json", "r") as f:
            content = f.read().strip()
            if not content:
                print("Trading decisions file is empty")
                return holdings, claude_actions, cash
            decisions_data = json.loads(content)
        
        if not decisions_data.get("execution_queue"):
            print("No pending trading decisions")
            return holdings, claude_actions, cash
        
        print("🤖 Checking Claude's trading decisions...")
        
        for order in decisions_data["execution_queue"]:
            symbol = order["symbol"]
            action = order["action"]
            
            if action == "SELL_ALL":
                if symbol in holdings and holdings[symbol] > 0:
                    shares_to_sell = holdings[symbol]
                    if symbol in prices:
                        proceeds = shares_to_sell * prices[symbol]
                        cash += proceeds
                        holdings[symbol] = 0
                        action_msg = f"SELL ALL {symbol}: {shares_to_sell} shares @ ${prices[symbol]:.4f} = ${proceeds:.2f}"
                        claude_actions.append(action_msg)
                        print(f"✅ Executed: {action_msg}")
            
            elif action == "TRIM_TO":
                target_qty = order["target_quantity"]
                if symbol in holdings and holdings[symbol] > target_qty:
                    shares_to_sell = holdings[symbol] - target_qty
                    if symbol in prices:
                        proceeds = shares_to_sell * prices[symbol]
                        cash += proceeds
                        holdings[symbol] = target_qty
                        action_msg = f"TRIM {symbol} to {target_qty} shares - ${proceeds:.2f} proceeds"
                        claude_actions.append(action_msg)
                        print(f"✅ Executed: {action_msg}")
            
            elif action == "BUY_NEW":
                target_value = order.get("target_value", 0)
                if target_value > 0 and cash >= target_value:
                    if symbol in prices and prices[symbol] > 0:
                        shares_to_buy = int(target_value / prices[symbol])
                        cost = shares_to_buy * prices[symbol]
                        if cost <= cash:
                            cash -= cost
                            holdings[symbol] = holdings.get(symbol, 0) + shares_to_buy
                            action_msg = f"BUY {symbol}: {shares_to_buy} shares @ ${prices[symbol]:.4f} = ${cost:.2f}"
                            claude_actions.append(action_msg)
                            print(f"✅ Executed: {action_msg}")
            
            elif action == "HOLD":
                print(f"📊 HOLD {symbol}: {order.get('current_quantity', 0)} shares")
        
        # Mark decisions as executed
        decisions_data["claude_decisions_executed"] = True
        decisions_data["execution_date"] = date
        decisions_data["execution_queue"] = []
        
        save_json("trading_decisions.json", decisions_data)
        
    except FileNotFoundError:
        print("No trading decisions file found")
    except json.JSONDecodeError as e:
        print(f"JSON decode error in trading_decisions.json: {e}")
        print("Please check the JSON syntax")
    except Exception as e:
        print(f"Error executing trading decisions: {e}")
    
    return holdings, claude_actions, cash

def main():
    SYMBOLS = ["GEVO", "FEIM", "ARQ", "UPXI", "SERV", "MYOMO", "CABA"]
    
    # Use the correct environment variable name
    API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY")
    
    if not API_KEY:
        print("Error: ALPHAVANTAGE_API_KEY environment variable not set")
        return 1
    
    print(f"✅ Using API key: {API_KEY[:8]}...")
    
    os.makedirs("data", exist_ok=True)
    os.makedirs("docs", exist_ok=True)
    
    try:
        with open("data/holdings.json", "r") as f:
            holdings = json.load(f)
    except FileNotFoundError:
        # Initialize with your current portfolio from August 18
        print("🔧 Initializing holdings with current portfolio...")
        holdings = {
            "GEVO": 199,
            "FEIM": 10, 
            "ARQ": 37,
            "UPXI": 17,
            "SERV": 0,
            "MYOMO": 0,
            "CABA": 0
        }
    
    try:
        with open("data/cash.json", "r") as f:
            cash_data = json.load(f)
            cash = cash_data.get("cash", 0.0)
    except FileNotFoundError:
        # Initialize with your current cash from August 18
        print("🔧 Initializing cash with current amount...")
        cash = 180.00
    
    print(f"📊 Loading existing holdings and cash...")
    
    try:
        with open("data/holdings.json", "r") as f:
            holdings = json.load(f)
            print(f"✅ Loaded holdings: {holdings}")
    except FileNotFoundError:
        # Initialize with your current portfolio from August 18
        print("🔧 Initializing holdings with current portfolio...")
        holdings = {
            "GEVO": 199,
            "FEIM": 10, 
            "ARQ": 37,
            "UPXI": 17,
            "SERV": 0,
            "MYOMO": 0,
            "CABA": 0
        }
        print(f"🔧 Initialized holdings: {holdings}")
    
    try:
        with open("data/cash.json", "r") as f:
            cash_data = json.load(f)
            cash = cash_data.get("cash", 0.0)
            print(f"✅ Loaded cash: ${cash:.2f}")
    except FileNotFoundError:
        # Initialize with your current cash from August 18
        print("🔧 Initializing cash with current amount...")
        cash = 180.00
        print(f"🔧 Initialized cash: ${cash:.2f}")
    
    print(f"\n📈 Fetching current stock prices...")
    
    prices = {}
    for symbol in SYMBOLS:
        print(f"Fetching {symbol}...")
        price = get_alpha_vantage_price(symbol, API_KEY)
        if price:
            prices[symbol] = price
            print(f"Fetched {symbol}: ${price:.4f}")
        else:
            print(f"Failed to fetch {symbol}")
    
    print(f"\n🔄 Processing trading decisions...")
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    holdings, claude_actions, cash = execute_trading_decisions(holdings, prices, current_date, cash)
    
    print(f"\n💰 Calculating portfolio values...")
    print(f"Holdings after trading: {holdings}")
    print(f"Cash after trading: ${cash:.2f}")
    
    values = {}
    total_value = cash
    
    for symbol in SYMBOLS:
        if symbol in holdings and symbol in prices and holdings[symbol] > 0:
            value = holdings[symbol] * prices[symbol]
            values[symbol] = f"{value:.2f}"
            total_value += value
            print(f"{symbol}: {holdings[symbol]} shares × ${prices[symbol]:.4f} = ${value:.2f}")
        else:
            values[symbol] = "0.00"
    
    print(f"\n💼 Total portfolio value: ${total_value:.2f}")
    
    previous_data = get_previous_day_data()
    
    current_data = {
        "date": current_date,
        "prices": {k: v for k, v in prices.items() if k in holdings and holdings[k] > 0},
        "quantities": {k: v for k, v in holdings.items() if v > 0},
        "total_value": str(total_value)
    }
    
    daily_changes = calculate_daily_changes(current_data, previous_data)
    
    portfolio_data = {
        "date": current_date,
        "cash": f"{cash:.2f}",
        "total_value": f"{total_value:.2f}",
        "prices": {k: v for k, v in prices.items() if k in holdings and holdings[k] > 0},
        "quantities": {k: v for k, v in holdings.items() if v > 0},
        "values": {k: v for k, v in values.items() if k in holdings and holdings[k] > 0},
        "actions": claude_actions[0] if claude_actions else None,
        "daily_changes": daily_changes,
        "claude_decisions_executed": bool(claude_actions)
    }
    
    save_json("docs/latest.json", portfolio_data)
    
    save_json("data/holdings.json", holdings)
    save_json("data/cash.json", {"cash": cash})
    
    csv_row = {
        "date": current_date,
        "total_value": total_value,
        "cash": cash
    }
    
    for symbol in SYMBOLS:
        csv_row[f"{symbol}_price"] = prices.get(symbol, 0)
        csv_row[f"{symbol}_qty"] = holdings.get(symbol, 0)
        csv_row[f"{symbol}_value"] = float(values.get(symbol, 0))
    
    csv_file = "data/portfolio_history.csv"
    file_exists = os.path.isfile(csv_file)
    
    with open(csv_file, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(csv_row)
    
    report_lines = [
        "# Portfolio Report",
        f"**As of (latest close)**: {current_date}",
        ""
    ]
    
    for symbol in SYMBOLS:
        if symbol in holdings and holdings[symbol] > 0 and symbol in prices:
            price = prices[symbol]
            qty = holdings[symbol]
            value = qty * price
            report_lines.append(f"- {symbol}: close {price:.4f}, qty {qty}, value ${value:.2f}")
    
    report_lines.extend([
        "",
        f"Cash: ${cash:.2f}",
        f"**Total value**: ${total_value:.2f}"
    ])
    
    if claude_actions:
        report_lines.extend([
            "",
            "## Recent Actions",
            ""
        ])
        for action in claude_actions:
            report_lines.append(f"- {action}")
    
    with open("docs/latest_report.md", "w") as f:
        f.write("\n".join(report_lines))
    
    print(f"\n📊 Portfolio updated successfully!")
    print(f"Total value: ${total_value:.2f}")
    print(f"Cash: ${cash:.2f}")
    if claude_actions:
        print(f"Claude actions executed: {len(claude_actions)}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

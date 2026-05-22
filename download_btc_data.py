"""
Download BTC historical data from CryptoCompare (free tier).
Timeframes: 5m, 15m, 1h, 4h, daily, weekly
Saves to data/ directory as CSV + JSON.
Author: Hermes | May 2026
"""

import os
import csv
import json
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('BTCData')

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

BASE_URL = "https://min-api.cryptocompare.com/data/v2"

# Timeframe config: (interval_in_minutes, max_candles_per_call, aggregate_multiplier)
TIMEFRAMES = {
    '5m':   ('histominute', 288, 5),     # 5m = 5-min aggregate, 288 candles = 24h
    '15m':  ('histominute', 288, 15),    # 15m = 15-min aggregate, 288 candles = 72h
    '1h':   ('histohour', 168, 1),       # 1h = hourly, 168 candles = 7 days
    '4h':   ('histohour', 168, 4),       # 4h = 4-hour aggregate, 168 candles = 28 days
    'daily': ('histoday', 365, 1),        # daily, 365 candles = 365 days
    'weekly': ('histoday', 365, 7),       # weekly = 7-day aggregate, 365 candles = 7 years
}


def fetch_candles(endpoint: str, fsym: str, tsym: str, limit: int, aggregate: int, to_ts: Optional[int] = None) -> Optional[List[Dict]]:
    """Fetch candles from CryptoCompare API."""
    url = f"{BASE_URL}/{endpoint}"
    params = {
        'fsym': fsym,
        'tsym': tsym,
        'limit': limit,
        'aggregate': aggregate,
    }
    if to_ts:
        params['toTs'] = to_ts
    
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('Response') == 'Success':
                return data['Data']['Data']
            else:
                logger.warning(f"API response: {data.get('Message', 'Unknown error')}")
        else:
            logger.warning(f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Fetch error: {e}")
    
    return None


def download_timeframe(tf: str, years: int = 5) -> List[Dict]:
    """Download full historical data for a timeframe."""
    endpoint, limit, aggregate = TIMEFRAMES[tf]
    
    # Calculate total candles needed
    if tf in ['5m', '15m']:
        # For minute data, we can only go back ~1 week per call with free tier
        # CryptoCompare limits: 2000 calls/hour, 200 calls/sec
        # We'll download incrementally
        total_days = years * 365
        calls_needed = (total_days * 24 * 60) // (limit * aggregate) + 1
    elif tf in ['1h', '4h']:
        total_days = years * 365
        calls_needed = (total_days * 24) // (limit * aggregate) + 1
    else:
        total_days = years * 365
        calls_needed = total_days // limit + 1
    
    logger.info(f"Downloading {tf}: ~{calls_needed} API calls needed for {years} years")
    
    all_candles = []
    to_ts = int(datetime.now().timestamp())
    
    for i in range(calls_needed):
        logger.info(f"  Call {i+1}/{calls_needed}: fetching up to {datetime.fromtimestamp(to_ts)}")
        
        candles = fetch_candles(endpoint, 'BTC', 'USD', limit, aggregate, to_ts)
        if not candles:
            logger.warning("No candles returned, stopping")
            break
        
        if len(candles) == 0:
            break
        
        all_candles.extend(candles)
        
        # Update to_ts for next batch (oldest candle - 1)
        oldest_ts = candles[0]['time']
        to_ts = oldest_ts - 1
        
        # Rate limit: max 200 calls/sec, so sleep a bit
        time.sleep(0.1)
        
        # Stop if we've gone back far enough
        oldest_date = datetime.fromtimestamp(oldest_ts)
        target_date = datetime.now() - timedelta(days=years*365)
        if oldest_date < target_date:
            logger.info(f"Reached target date: {oldest_date}")
            break
    
    logger.info(f"Downloaded {len(all_candles)} {tf} candles")
    return all_candles


def save_csv(candles: List[Dict], filename: str):
    """Save candles to CSV."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'open', 'high', 'low', 'close', 'volume_from', 'volume_to'])
        for c in candles:
            writer.writerow([
                datetime.fromtimestamp(c['time']).isoformat(),
                c['open'],
                c['high'],
                c['low'],
                c['close'],
                c.get('volumefrom', 0),
                c.get('volumeto', 0),
            ])
    logger.info(f"Saved CSV: {filepath}")


def save_json(candles: List[Dict], filename: str):
    """Save candles to JSON."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w') as f:
        json.dump(candles, f, indent=2)
    logger.info(f"Saved JSON: {filepath}")


def convert_to_timeframe(source_candles: List[Dict], tf: str) -> List[Dict]:
    """
    Convert daily candles to higher timeframes (weekly).
    Or downsample if needed.
    """
    if tf == 'weekly':
        # Aggregate daily into weekly
        weekly = []
        week_candles = []
        current_week = None
        
        for c in sorted(source_candles, key=lambda x: x['time']):
            dt = datetime.fromtimestamp(c['time'])
            week_key = dt.isocalendar()[:2]  # (year, week)
            
            if week_key != current_week:
                if week_candles:
                    weekly.append({
                        'time': week_candles[0]['time'],
                        'open': week_candles[0]['open'],
                        'high': max(c['high'] for c in week_candles),
                        'low': min(c['low'] for c in week_candles),
                        'close': week_candles[-1]['close'],
                        'volumefrom': sum(c.get('volumefrom', 0) for c in week_candles),
                        'volumeto': sum(c.get('volumeto', 0) for c in week_candles),
                    })
                week_candles = []
                current_week = week_key
            
            week_candles.append(c)
        
        # Add last week
        if week_candles:
            weekly.append({
                'time': week_candles[0]['time'],
                'open': week_candles[0]['open'],
                'high': max(c['high'] for c in week_candles),
                'low': min(c['low'] for c in week_candles),
                'close': week_candles[-1]['close'],
                'volumefrom': sum(c.get('volumefrom', 0) for c in week_candles),
                'volumeto': sum(c.get('volumeto', 0) for c in week_candles),
            })
        
        return weekly
    
    return source_candles


def main():
    logger.info("=" * 60)
    logger.info("BTC HISTORICAL DATA DOWNLOADER")
    logger.info("=" * 60)
    
    # Download daily first (most reliable, goes back furthest)
    logger.info("\n1. Downloading DAILY data (5 years)...")
    daily = download_timeframe('daily', years=5)
    save_csv(daily, 'btc_daily.csv')
    save_json(daily, 'btc_daily.json')
    
    # Convert to weekly
    logger.info("\n2. Converting to WEEKLY...")
    weekly = convert_to_timeframe(daily, 'weekly')
    save_csv(weekly, 'btc_weekly.csv')
    save_json(weekly, 'btc_weekly.json')
    
    # Download 4h (limited history with free tier)
    logger.info("\n3. Downloading 4H data (1 year max for free tier)...")
    h4 = download_timeframe('4h', years=1)
    save_csv(h4, 'btc_4h.csv')
    save_json(h4, 'btc_4h.json')
    
    # Download 1h (limited history)
    logger.info("\n4. Downloading 1H data (1 year max for free tier)...")
    h1 = download_timeframe('1h', years=1)
    save_csv(h1, 'btc_1h.csv')
    save_json(h1, 'btc_1h.json')
    
    # For 15m and 5m, free tier is very limited (~1 week)
    logger.info("\n5. Downloading 15M data (1 week max for free tier)...")
    m15 = download_timeframe('15m', years=0.02)  # ~1 week
    save_csv(m15, 'btc_15m.csv')
    save_json(m15, 'btc_15m.json')
    
    logger.info("\n6. Downloading 5M data (1 week max for free tier)...")
    m5 = download_timeframe('5m', years=0.02)
    save_csv(m5, 'btc_5m.csv')
    save_json(m5, 'btc_5m.json')
    
    logger.info("\n" + "=" * 60)
    logger.info("DOWNLOAD COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Daily:   {len(daily)} candles ({datetime.fromtimestamp(daily[0]['time']).date()} to {datetime.fromtimestamp(daily[-1]['time']).date()})")
    logger.info(f"Weekly:  {len(weekly)} candles")
    logger.info(f"4H:      {len(h4)} candles")
    logger.info(f"1H:      {len(h1)} candles")
    logger.info(f"15M:     {len(m15)} candles")
    logger.info(f"5M:      {len(m5)} candles")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()

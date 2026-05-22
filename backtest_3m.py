#!/usr/bin/env python3
"""
HERMES BACKTEST ENGINE v7 — 3-MONTH RANDOM COINS (Kraken 4h)
Uses CCXT + Kraken to fetch ~90 days of 4h OHLCV for 30 random altcoins.
"""

import json
import random
import time
from datetime import datetime
from typing import Dict, List, Optional

import ccxt

# ── OPTIMIZED STRATEGY PARAMS ──
STARTING_BALANCE  = 113.31
MAX_RISK_PCT      = 0.09
RISK_MULT         = 1.5
MAX_POSITION_PCT  = 0.22
MIN_TRADE_USD     = 5.0
FIXED_STOP_PCT    = 0.15
EXIT1_PCT         = 0.20
TRAIL_BASE        = 0.10
TRAIL_MID         = 0.08
TRAIL_TIGHT       = 0.06
PYRAMID_PCT       = 0.08
TIME_STOP_CANDLES = 9           # 9 × 4h = 36h

# Entry thresholds (adapted for 4h candles)
MIN_VOL_SURGE     = 2.0
MIN_CANDLE_CHG    = 8.0         # % move per 4h candle
MAX_24H_CHG       = 300.0       # 6-candle (24h) change cap
MIN_LIQ_USD       = 50_000      # liquidity proxy: min daily volume
MIN_VOL_ABS       = 60_000      # min volume per 4h candle (proxy)
MIN_SCORE         = 60
PRICE_SANITY      = 20.0

TARGET_SYMBOLS    = 30
MIN_CANDLES       = 500

STABLE_KEYWORDS   = {"USDC", "USDT", "USD", "DAI", "FRAX", "BUSD", "TUSD", "USDE", "EUR", "GBP", "JPY", "CHF"}

# ── EXCHANGE ──

exchange = ccxt.kraken({'enableRateLimit': True})

def fetch_candles(symbol: str, limit: int = 540) -> List[list]:
    """Fetch 4h OHLCV from Kraken. Returns list of [timestamp, open, high, low, close, volume]."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=limit)
        return ohlcv
    except Exception as e:
        print(f"  [WARN] {symbol} fetch failed: {e}")
        return []


# ── INDICATORS ──

def ch_pct(candles: List[list], idx: int, back: int) -> float:
    if idx < back:
        return 0.0
    p = candles[idx - back][4]
    c = candles[idx][4]
    return (c - p) / p * 100 if p > 0 else 0.0


def vol_surge_ratio(candles: List[list], idx: int) -> float:
    """Recent 6 candles (24h) avg vs prior 18 candles (72h) avg."""
    if idx < 6:
        return 1.0
    recent = [c[5] for c in candles[max(0, idx - 6):idx + 1]]
    prior = [c[5] for c in candles[max(0, idx - 24):idx - 6]]
    r = sum(recent) / len(recent) if recent else 0
    p = sum(prior) / len(prior) if prior else r
    return r / p if p > 0 else 1.0


# ── ENTRY SIGNAL ──

def evaluate_entry(candles: List[list], idx: int, avg_vol_24h: float) -> Optional[dict]:
    if idx < 12:
        return None

    price = candles[idx][4]
    vol = candles[idx][5]
    if price <= 0:
        return None

    ch1c = ch_pct(candles, idx, 1)           # 1-candle (4h) change
    ch6c = ch_pct(candles, idx, 6)           # 6-candle (~24h) change
    vsurge = vol_surge_ratio(candles, idx)

    # Hard gates
    if ch6c > MAX_24H_CHG:
        return None
    if ch1c < MIN_CANDLE_CHG:
        return None
    if vol < MIN_VOL_ABS:
        return None
    if vsurge < MIN_VOL_SURGE:
        return None
    if avg_vol_24h > 0 and avg_vol_24h * 6 < MIN_LIQ_USD:
        return None

    # BOS: price > 6-candle swing high
    swing_hi = max(c[2] for c in candles[max(0, idx - 7):idx])
    bos = price > swing_hi * 1.002

    # Liquidity sweep: prior candle wicked down then bounced
    swept = (candles[idx - 1][3] < candles[idx - 1][4] * 0.97 and ch1c > 2)

    # AMD proxy
    prior_ch = ch_pct(candles, idx - 1, 1)
    amd = abs(prior_ch) >= 5.0 and ch1c > 0

    if not (bos or swept):
        return None

    score = 40 if bos else 0
    score += 25 if swept else 0
    score += 15 if amd else 0
    score += min(vsurge / MIN_VOL_SURGE * 10, 15)

    if score < MIN_SCORE:
        return None

    return {
        'price': price, 'vsurge': vsurge,
        'ch1c': ch1c, 'ch24h': ch6c,
        'score': score, 'bos': bos, 'swept': swept, 'amd': amd,
    }


# ── POSITION SIZING ──

def calc_position(balance: float, entry: float) -> dict:
    size = balance * MAX_RISK_PCT * RISK_MULT
    size = max(size, MIN_TRADE_USD)
    size = min(size, balance * MAX_POSITION_PCT)
    qty = size / entry
    stop = entry * (1 - FIXED_STOP_PCT)
    return {'size': size, 'qty': qty, 'stop': stop}


# ── SIMULATE ONE TOKEN ──

def simulate_token(sym: str, candles: List[list], bal: List[float]) -> List[dict]:
    trades = []
    in_trade = False
    entry_price = stop_price = 0.0
    size = qty = 0.0
    entry_idx = 0
    highest = 0.0
    e1_taken = False
    rem_qty = rem_size = partial_pnl = 0.0
    last_exit = -5
    MAX_PER_TOKEN = 3

    for i, c in enumerate(candles):
        lo, hi, close = c[3], c[2], c[4]

        if in_trade:
            if close > highest:
                highest = close

            if entry_price > 0 and close > entry_price * PRICE_SANITY:
                close = entry_price * PRICE_SANITY
                hi = close

            upct = (close - entry_price) / entry_price

            # Progressive trail after Exit 1
            if e1_taken:
                if upct >= 1.0:
                    trail = TRAIL_TIGHT
                elif upct >= 0.50:
                    trail = TRAIL_MID
                else:
                    trail = TRAIL_BASE
                trail_level = highest * (1 - trail)
                trail_level = max(trail_level, entry_price * 1.02)
                if trail_level > stop_price:
                    stop_price = trail_level

            # Stop loss
            if lo <= stop_price:
                exit_px = stop_price
                pnl = partial_pnl + (rem_qty * exit_px - rem_size)
                trades.append(_t(sym, entry_price, exit_px, size, pnl,
                                 'stop', i - entry_idx, highest, candles[entry_idx][0]))
                bal[0] += pnl
                in_trade = False
                last_exit = i
                continue

            # Time stop
            if (i - entry_idx) >= TIME_STOP_CANDLES:
                pnl = partial_pnl + (rem_qty * close - rem_size)
                trades.append(_t(sym, entry_price, close, size, pnl,
                                 'time_stop', i - entry_idx, highest, candles[entry_idx][0]))
                bal[0] += pnl
                in_trade = False
                last_exit = i
                continue

            # Exit 1: sell 50% at +20%, add pyramid
            if not e1_taken and close >= entry_price * (1 + EXIT1_PCT):
                sq = qty * 0.50
                ss = size * 0.50
                partial_pnl += sq * close - ss
                rem_qty -= sq
                rem_size -= ss
                stop_price = max(stop_price, entry_price * 1.02)
                e1_taken = True

                # Pyramid
                pyr_sz = min(bal[0] * PYRAMID_PCT, bal[0] * 0.15)
                pyr_sz = max(pyr_sz, 0.0)
                if pyr_sz >= 5.0 and bal[0] > pyr_sz * 1.5:
                    pyr_qty = pyr_sz / close if close > 0 else 0
                    bal[0] -= pyr_sz
                    rem_qty += pyr_qty
                    rem_size += pyr_sz
                    size += pyr_sz

        else:
            if len(trades) >= MAX_PER_TOKEN:
                break
            if (i - last_exit) < 6:
                continue
            # avg volume proxy for liquidity gate
            avg_vol_24h = sum(c[5] for c in candles[max(0, i-6):i+1]) / 6.0 if i >= 6 else 0.0
            sig = evaluate_entry(candles, i, avg_vol_24h)
            if sig:
                pos = calc_position(bal[0], sig['price'])
                entry_price = sig['price']
                stop_price = pos['stop']
                size = pos['size']
                qty = pos['qty']
                rem_qty = qty
                rem_size = size
                partial_pnl = 0.0
                highest = entry_price
                entry_idx = i
                e1_taken = False
                in_trade = True

    return trades


def _t(sym, entry, exit_p, size, pnl, reason, hold, highest, ets):
    return {
        'sym': sym,
        'entry': entry,
        'exit': exit_p,
        'size': size,
        'pnl': pnl,
        'pnl_pct': pnl / size if size > 0 else 0,
        'reason': reason,
        'hold_h': hold * 4,
        'peak_x': highest / entry if entry > 0 else 1,
        'entry_dt': datetime.fromtimestamp(ets // 1000).strftime('%Y-%m-%d'),
    }


# ── MONTE CARLO ──

def monte_carlo(wr: float, avg_win_pct: float, avg_loss_pct: float,
                trades_mo: float, months: int = 3, sims: int = 2000) -> dict:
    results = []
    for _ in range(sims):
        b = STARTING_BALANCE
        for _ in range(int(trades_mo * months)):
            sz = min(b * MAX_RISK_PCT * RISK_MULT, b * MAX_POSITION_PCT)
            b += sz * (avg_win_pct if random.random() < wr else avg_loss_pct)
            if b < 1.0:
                b = 0.0
                break
        results.append(b)
    results.sort()
    s = len(results)
    return {
        'p10': results[s // 10],
        'p50': results[s // 2],
        'p90': results[int(s * 0.9)],
        'ruin': sum(1 for r in results if r < 10) / s,
        'months': months,
    }


# ── REPORT ──

def print_report(all_trades: List[dict], tested: int, skipped: int):
    if not all_trades:
        print("No trades fired — check data or loosen entry filters.")
        return

    wins = [t for t in all_trades if t['pnl'] > 0]
    losses = [t for t in all_trades if t['pnl'] <= 0]
    n = len(all_trades)
    wr = len(wins) / n

    avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
    avg_win_pct = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss_pct = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    rr = abs(avg_win / avg_loss) if avg_loss else 0
    expectancy = wr * avg_win + (1 - wr) * avg_loss

    bal = STARTING_BALANCE
    peak = bal
    max_dd = consec = max_c = 0
    for t in all_trades:
        bal += t['pnl']
        peak = max(peak, bal)
        dd = (peak - bal) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
        if t['pnl'] < 0:
            consec += 1
            max_c = max(max_c, consec)
        else:
            consec = 0
    final = STARTING_BALANCE + sum(t['pnl'] for t in all_trades)

    reasons: Dict[str, int] = {}
    for t in all_trades:
        reasons[t['reason']] = reasons.get(t['reason'], 0) + 1

    p2 = sum(1 for t in wins if t['peak_x'] >= 2)
    p15 = sum(1 for t in wins if t['peak_x'] >= 1.5)

    mc = monte_carlo(wr, avg_win_pct, avg_loss_pct,
                     trades_mo=max(n / max(tested, 1) * 3, 6), months=3)

    print(f"\n{'='*65}")
    print(f"RESULTS  ({n} trades / {tested} tokens / {tested+skipped} tried)")
    print(f"{'='*65}")
    print(f"  Win rate:          {wr*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg win:          +${avg_win:.2f}  ({avg_win_pct*100:+.1f}%)")
    print(f"  Avg loss:         -${abs(avg_loss):.2f}  ({avg_loss_pct*100:+.1f}%)")
    print(f"  Reward/Risk:       {rr:.2f}×")
    print(f"  Expectancy:       ${expectancy:+.2f}/trade")
    print()
    print(f"  Start:  ${STARTING_BALANCE:.2f}   End: ${final:.2f}   Max DD: {max_dd:.1%}")
    print(f"  Max consec losses: {max_c}")
    print()
    print(f"  Peak multiples on winners:")
    if wins:
        print(f"    ≥1.5× entry:  {p15}/{len(wins)} ({p15/len(wins)*100:.0f}%)")
        print(f"    ≥2.0× entry:  {p2}/{len(wins)}  ({p2/len(wins)*100:.0f}%)")
    print()
    print("  Exit breakdown:")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {r:20s}: {c:3d}  ({c/n*100:.0f}%)")
    print()
    print("  Top winners:")
    for t in sorted(wins, key=lambda x: -x['pnl'])[:6]:
        print(f"    {t['sym']:12s} +${t['pnl']:.2f} ({t['pnl_pct']*100:+.0f}%)  "
              f"peak={t['peak_x']:.1f}×  {t['entry_dt']}")
    print()
    print("  Top losers:")
    for t in sorted(losses, key=lambda x: x['pnl'])[:5]:
        print(f"    {t['sym']:12s}  -${abs(t['pnl']):.2f} ({t['pnl_pct']*100:+.0f}%)  {t['entry_dt']}")

    print()
    print(f"  MONTE CARLO — 3mo projection (~{mc['months']} mo, 2000 sims):")
    print(f"    Worst 10%:  ${mc['p10']:.0f}   Median: ${mc['p50']:.0f}   Best 10%: ${mc['p90']:.0f}")
    print(f"    Blowup risk (<$10): {mc['ruin']*100:.1f}%")

    print()
    print(f"{'='*65}")
    be_wr = 1 / (1 + rr) if rr > 0 else 1.0
    if expectancy > 0 and wr >= 0.35 and rr >= 1.4:
        v = "VIABLE  — deploy with full position sizing"
    elif expectancy > 0:
        v = "MARGINAL — positive EV, but needs more data to confirm"
    elif rr >= 2.0:
        v = "POSSIBLE — R:R is good, improve win rate via stricter entry"
    else:
        v = f"NOT VIABLE — need WR>{be_wr*100:.0f}% or R:R>{1/(wr)-1:.1f}× to break even"
    print(f"  VERDICT: {v}")
    print(f"{'='*65}")

    with open('backtest_3m_results.json', 'w') as f:
        json.dump({
            'meta': {
                'run_date': datetime.now().isoformat(),
                'strategy': 'v7_3month_kraken_4h',
                'exit1_pct': EXIT1_PCT,
                'trail_base': TRAIL_BASE,
                'trail_mid': TRAIL_MID,
                'trail_tight': TRAIL_TIGHT,
                'stop_pct': FIXED_STOP_PCT,
                'position_pct': MAX_RISK_PCT * RISK_MULT,
                'pyramid_pct': PYRAMID_PCT,
                'aggregate_hours': 4,
                'time_stop_candles': TIME_STOP_CANDLES,
            },
            'summary': {
                'trades': n,
                'win_rate': wr * 100,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'rr': rr,
                'expectancy': expectancy,
                'max_dd': max_dd,
                'final_balance': final,
            },
            'monte_carlo': mc,
            'trades': all_trades,
        }, f, indent=2, default=str)
    print("\n  Saved → backtest_3m_results.json")


# ── MAIN ──

def run_backtest():
    print("=" * 65)
    print("HERMES BACKTEST v7 — 3-MONTH RANDOM COINS (Kraken 4h)")
    print(f"  Balance: ${STARTING_BALANCE:.2f} | Position: {MAX_RISK_PCT*RISK_MULT*100:.0f}% per trade")
    print(f"  Exit 1: sell 50% at +{EXIT1_PCT*100:.0f}%  |  Trail: {TRAIL_BASE*100:.0f}%→{TRAIL_MID*100:.0f}%→{TRAIL_TIGHT*100:.0f}% progressive")
    print(f"  Pyramid: +{PYRAMID_PCT*100:.0f}% on Exit 1 confirm  |  Stop: -{FIXED_STOP_PCT*100:.0f}%  |  Time: {TIME_STOP_CANDLES*4}h")
    print(f"  Entry: 4h>{MIN_CANDLE_CHG}%, surge>{MIN_VOL_SURGE}×, BOS")
    print(f"  Symbols: up to {TARGET_SYMBOLS} random USD pairs")
    print("=" * 65)

    print("\nLoading Kraken markets ...")
    markets = exchange.load_markets()
    symbols = [
        s for s, m in markets.items()
        if m.get('quote') == 'USD'
        and m.get('spot', False)
        and not any(stable in m.get('base', '').upper() for stable in STABLE_KEYWORDS)
        and '2L' not in m.get('base', '') and '2S' not in m.get('base', '')
        and '3L' not in m.get('base', '') and '3S' not in m.get('base', '')
        and '5L' not in m.get('base', '') and '5S' not in m.get('base', '')
    ]
    print(f"Eligible USD pairs: {len(symbols)}")
    random.shuffle(symbols)
    candidates = symbols[:80]  # test up to 80 to find 30 with data

    all_trades: List[dict] = []
    bal = [STARTING_BALANCE]
    tested = skipped = 0

    for sym in candidates:
        if tested >= TARGET_SYMBOLS:
            break
        candles = fetch_candles(sym, limit=540)
        if len(candles) < MIN_CANDLES:
            skipped += 1
            continue
        tested += 1
        trades = simulate_token(sym, candles, bal)
        if trades:
            all_trades.extend(trades)
            w = sum(1 for t in trades if t['pnl'] > 0)
            p = sum(t['pnl'] for t in trades)
            print(f"  {sym:12s} | {len(trades):3d} trades | {w}/{len(trades)} W | PnL ${p:+.2f}")
        else:
            print(f"  {sym:12s} | 0 trades")

    print_report(all_trades, tested, skipped)


if __name__ == '__main__':
    run_backtest()

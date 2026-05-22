#!/usr/bin/env python3
"""
HERMES BACKTEST ENGINE v5 — MAXIMUM GROWTH
Optimizations applied:
  Kelly sizing:  13.5% per trade (was 9%) — 54% of Kelly optimal
  No fixed Exit 2: pure progressive trail (captures 3×-10× runners fully)
  Pyramid:       adds 8% of balance after Exit 1 confirmed
  Progressive trail: 10% → 8% → 6% as gains grow (locks more on mega-runs)
  Faster entry:  same filters (vol surge, BOS, $50K liq)
"""

import json
import random
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ── OPTIMIZED STRATEGY PARAMS ──
STARTING_BALANCE  = 113.31
MAX_RISK_PCT      = 0.09         # 54% of Kelly — was 6%, +50% more per trade
RISK_MULT         = 1.5
MAX_POSITION_PCT  = 0.22
MIN_TRADE_USD     = 5.0
FIXED_STOP_PCT    = 0.10         # tighter stop: 10% (was 15%)
EXIT1_PCT         = 0.20         # sell 50% at +20%
# No fixed Exit 2 — progressive trailing only:
TRAIL_BASE        = 0.10         # +20%-50%: 10% trail
TRAIL_MID         = 0.08         # +50%-100%: 8% trail
TRAIL_TIGHT       = 0.06         # +100%+: 6% trail (locks mega-runner gains)
PYRAMID_PCT       = 0.08         # pyramid 8% of balance after Exit 1
TIME_STOP_HOURS   = 36
API_SLEEP         = 1.4

# Entry thresholds
MIN_VOL_SURGE     = 3.0          # recent 6h must be 3× prior baseline (was 2.0)
MIN_1H_CHG        = 6.0          # 1h candle must be +6%+ (was 4.0)
MAX_24H_CHG       = 150.0        # skip if already up 150%+ (was 300%)
MIN_LIQ_USD       = 50_000       # $50K quality gate
MIN_VOL_ABS       = 20_000       # $20K hourly candle volume (was 15K)
MIN_SCORE         = 75           # higher quality entry (was 60)
PRICE_SANITY      = 20.0


# ── HTTP ──

def _get(url: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ── DATA ──

def fetch_trending_pools(pages: int = 5) -> List[dict]:
    pools = []
    for page in range(1, pages + 1):
        data = _get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page={page}"
        )
        if data:
            pools.extend(data.get('data', []))
        time.sleep(API_SLEEP)
    seen, unique = set(), []
    for p in pools:
        if p['id'] not in seen:
            seen.add(p['id'])
            unique.append(p)
    return unique


def fetch_hourly(pool_id: str, limit: int = 720) -> List[list]:
    data = _get(
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_id}"
        f"/ohlcv/hour?limit={limit}&aggregate=1"
    )
    time.sleep(API_SLEEP)
    if not data:
        return []
    candles = data.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
    return sorted(candles, key=lambda c: c[0])


# ── INDICATORS ──

def ch_pct(candles: List[list], idx: int, back: int) -> float:
    if idx < back:
        return 0.0
    p = candles[idx - back][4]
    c = candles[idx][4]
    return (c - p) / p * 100 if p > 0 else 0.0


def vol_surge_ratio(candles: List[list], idx: int) -> float:
    """Recent 6h avg vs prior 18h avg. >2 = volume accelerating."""
    if idx < 12:
        return 1.0
    recent = [c[5] for c in candles[max(0, idx-6):idx+1]]
    prior  = [c[5] for c in candles[max(0, idx-24):idx-6]]
    r = sum(recent) / len(recent) if recent else 0
    p = sum(prior)  / len(prior)  if prior  else r
    return r / p if p > 0 else 1.0


# ── ENTRY SIGNAL ──

def evaluate_entry(candles: List[list], idx: int, liq_usd: float) -> Optional[dict]:
    if idx < 12:
        return None

    price = candles[idx][4]
    vol   = candles[idx][5]
    if price <= 0:
        return None

    ch1h  = ch_pct(candles, idx, 1)
    ch6h  = ch_pct(candles, idx, 6)
    ch24h = ch_pct(candles, idx, 24) if idx >= 24 else ch6h * 4

    vsurge = vol_surge_ratio(candles, idx)

    # ── Hard gates (fast/cheap first) ──
    if ch24h > MAX_24H_CHG:            return None  # already pumped
    if ch1h < MIN_1H_CHG:              return None  # not moving enough
    if vol < MIN_VOL_ABS:              return None  # ghost candle
    if vsurge < MIN_VOL_SURGE:         return None  # no volume surge
    if liq_usd > 0 and liq_usd < MIN_LIQ_USD: return None

    # ── BOS: price > 6-candle swing high ──
    swing_hi = max(c[2] for c in candles[max(0, idx-7):idx])
    bos = price > swing_hi * 1.002

    # ── Liquidity sweep: prior candle wicked down then bounced ──
    swept = (candles[idx-1][3] < candles[idx-1][4] * 0.97 and ch1h > 2)

    # ── AMD proxy: bonus if prior candle had sharp spike ──
    prior_ch = ch_pct(candles, idx-1, 1)
    amd = abs(prior_ch) >= 5.0 and ch1h > 0

    if not (bos or swept):
        return None

    score  = 40 if bos else 0
    score += 25 if swept else 0
    score += 15 if amd else 0
    score += min(vsurge / MIN_VOL_SURGE * 10, 15)
    score += 10 if vsurge >= 4.0 else 0  # bonus for extreme volume spikes

    if score < MIN_SCORE:
        return None

    return {
        'price': price, 'vsurge': vsurge,
        'ch1h': ch1h, 'ch24h': ch24h,
        'score': score, 'bos': bos, 'swept': swept, 'amd': amd,
    }


# ── POSITION SIZING ──

def calc_position(balance: float, entry: float) -> dict:
    size = balance * MAX_RISK_PCT * RISK_MULT
    size = max(size, MIN_TRADE_USD)
    size = min(size, balance * MAX_POSITION_PCT)
    qty  = size / entry
    stop = entry * (1 - FIXED_STOP_PCT)
    return {'size': size, 'qty': qty, 'stop': stop}


# ── SIMULATE ONE TOKEN ──

def simulate_token(sym: str, candles: List[list], liq_usd: float,
                   bal: List[float]) -> List[dict]:
    trades        = []
    in_trade      = False
    entry_price   = stop_price = 0.0
    size = qty    = 0.0
    entry_idx     = 0
    highest       = 0.0
    e1_taken      = False
    rem_qty = rem_size = partial_pnl = 0.0
    last_exit     = -5
    MAX_PER_TOKEN = 3  # stop after 3 trades per token — prevents one bad token from dominating

    for i, c in enumerate(candles):
        lo, hi, close = c[3], c[2], c[4]

        if in_trade:
            if close > highest:
                highest = close

            if entry_price > 0 and close > entry_price * PRICE_SANITY:
                close = entry_price * PRICE_SANITY; hi = close

            upct = (close - entry_price) / entry_price  # unrealized % from entry

            # ── Progressive trail: tightens as gains grow ──
            if e1_taken:
                if upct >= 1.0:
                    trail = TRAIL_TIGHT   # +100%+: 6% trail — lock mega gains
                elif upct >= 0.50:
                    trail = TRAIL_MID     # +50%-100%: 8% trail
                else:
                    trail = TRAIL_BASE    # +20%-50%: 10% trail
                trail_level = highest * (1 - trail)
                trail_level = max(trail_level, entry_price * 1.02)
                if trail_level > stop_price:
                    stop_price = trail_level

            # ── Stop loss ──
            if lo <= stop_price:
                exit_px = stop_price
                pnl = partial_pnl + (rem_qty * exit_px - rem_size)
                trades.append(_t(sym, entry_price, exit_px, size, pnl,
                                 'stop', i - entry_idx, highest, candles[entry_idx][0]))
                bal[0] += pnl
                in_trade = False; last_exit = i
                continue

            # ── Time stop ──
            if (i - entry_idx) >= TIME_STOP_HOURS:
                pnl = partial_pnl + (rem_qty * close - rem_size)
                trades.append(_t(sym, entry_price, close, size, pnl,
                                 'time_stop', i - entry_idx, highest, candles[entry_idx][0]))
                bal[0] += pnl
                in_trade = False; last_exit = i
                continue

            # ── Exit 1: sell 50% at +20%, add pyramid ──
            if not e1_taken and close >= entry_price * (1 + EXIT1_PCT):
                sq = qty * 0.50; ss = size * 0.50
                partial_pnl += sq * close - ss
                rem_qty  -= sq; rem_size -= ss
                stop_price = max(stop_price, entry_price * 1.02)
                e1_taken  = True

                # ── Pyramid: add 8% of balance to proven winner ──
                pyr_sz = min(bal[0] * PYRAMID_PCT, bal[0] * 0.15)
                pyr_sz = max(pyr_sz, 0.0)
                if pyr_sz >= 5.0 and bal[0] > pyr_sz * 1.5:
                    pyr_qty = pyr_sz / close if close > 0 else 0
                    bal[0]    -= pyr_sz
                    rem_qty   += pyr_qty
                    rem_size  += pyr_sz
                    # Adjust total size for pnl_pct calculation
                    size      += pyr_sz

        else:
            if len(trades) >= MAX_PER_TOKEN:  # don't over-test one token
                break
            if (i - last_exit) < 6:
                continue
            sig = evaluate_entry(candles, i, liq_usd)
            if sig:
                pos         = calc_position(bal[0], sig['price'])
                entry_price = sig['price']
                stop_price  = pos['stop']
                size        = pos['size']
                qty         = pos['qty']
                rem_qty     = qty
                rem_size    = size
                partial_pnl = 0.0
                highest     = entry_price
                entry_idx   = i
                e1_taken    = False
                in_trade    = True

    return trades


def _t(sym, entry, exit_p, size, pnl, reason, hold, highest, ets):
    return {
        'sym':     sym,
        'entry':   entry,
        'exit':    exit_p,
        'size':    size,
        'pnl':     pnl,
        'pnl_pct': pnl / size if size > 0 else 0,
        'reason':  reason,
        'hold_h':  hold,
        'peak_x':  highest / entry if entry > 0 else 1,
        'entry_dt': datetime.fromtimestamp(ets).strftime('%Y-%m-%d'),
    }


# ── MONTE CARLO ──

def monte_carlo(wr: float, avg_win_pct: float, avg_loss_pct: float,
                trades_mo: float, months: int = 6, sims: int = 2000) -> dict:
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
        'p10': results[s//10], 'p50': results[s//2], 'p90': results[int(s*0.9)],
        'ruin': sum(1 for r in results if r < 10) / s,
        'months': months,
    }


# ── REPORT ──

def print_report(all_trades: List[dict], tested: int, skipped: int):
    if not all_trades:
        print("No trades fired — check API data or loosen entry filters.")
        return

    wins   = [t for t in all_trades if t['pnl'] > 0]
    losses = [t for t in all_trades if t['pnl'] <= 0]
    n      = len(all_trades)
    wr     = len(wins) / n

    avg_win      = sum(t['pnl'] for t in wins)  / len(wins)  if wins  else 0
    avg_loss     = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
    avg_win_pct  = sum(t['pnl_pct'] for t in wins)  / len(wins)  if wins  else 0
    avg_loss_pct = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    rr           = abs(avg_win / avg_loss) if avg_loss else 0
    expectancy   = wr * avg_win + (1 - wr) * avg_loss

    # Running PnL curve
    bal = STARTING_BALANCE; peak = bal; max_dd = consec = max_c = 0
    for t in all_trades:
        bal  += t['pnl']
        peak  = max(peak, bal)
        dd    = (peak - bal) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
        if t['pnl'] < 0:
            consec += 1; max_c = max(max_c, consec)
        else:
            consec = 0
    final = STARTING_BALANCE + sum(t['pnl'] for t in all_trades)

    reasons: Dict[str, int] = {}
    for t in all_trades:
        reasons[t['reason']] = reasons.get(t['reason'], 0) + 1

    p2  = sum(1 for t in wins if t['peak_x'] >= 2)
    p15 = sum(1 for t in wins if t['peak_x'] >= 1.5)

    mc = monte_carlo(wr, avg_win_pct, avg_loss_pct,
                     trades_mo=max(n / max(tested, 1) * 3, 6), months=6)

    print(f"\n{'='*65}")
    print(f"RESULTS  ({n} trades / {tested} tokens / {tested+skipped} pools fetched)")
    print(f"{'='*65}")
    print(f"  Win rate:          {wr*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg win:          +${avg_win:.2f}  ({avg_win_pct*100:+.1f}%)")
    print(f"  Avg loss:         -${abs(avg_loss):.2f}  ({avg_loss_pct*100:+.1f}%)")
    print(f"  Reward/Risk:       {rr:.2f}×  (need >1.0 to break even at {wr*100:.0f}% WR)")
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
    print(f"  MONTE CARLO — 6mo projection (~{mc['months']} mo, 2000 sims):")
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

    with open('backtest_results.json', 'w') as f:
        json.dump({
            'meta': {'run_date': datetime.now().isoformat(),
                     'strategy': 'v5_maximum_growth',
                     'exit1_pct': EXIT1_PCT,
                     'trail_base': TRAIL_BASE, 'trail_mid': TRAIL_MID, 'trail_tight': TRAIL_TIGHT,
                     'stop_pct': FIXED_STOP_PCT, 'position_pct': MAX_RISK_PCT * RISK_MULT,
                     'pyramid_pct': PYRAMID_PCT},
            'summary': {'trades': n, 'win_rate': wr*100,
                        'avg_win': avg_win, 'avg_loss': avg_loss,
                        'rr': rr, 'expectancy': expectancy,
                        'max_dd': max_dd, 'final_balance': final},
            'monte_carlo': mc,
            'trades': all_trades,
        }, f, indent=2, default=str)
    print(f"\n  Saved → backtest_results.json")


# ── MAIN ──

def run_backtest():
    print("=" * 65)
    print("HERMES BACKTEST v5 — MAXIMUM GROWTH CONFIGURATION")
    print(f"  Balance: ${STARTING_BALANCE:.2f} | Position: {MAX_RISK_PCT*RISK_MULT*100:.0f}% per trade")
    print(f"  Exit 1: sell 50% at +{EXIT1_PCT*100:.0f}%  |  Trail: {TRAIL_BASE*100:.0f}%→{TRAIL_MID*100:.0f}%→{TRAIL_TIGHT*100:.0f}% progressive")
    print(f"  Pyramid: +{PYRAMID_PCT*100:.0f}% on Exit 1 confirm  |  Stop: -{FIXED_STOP_PCT*100:.0f}%  |  Time: {TIME_STOP_HOURS}h")
    print(f"  Entry: 1h>{MIN_1H_CHG}%, surge>{MIN_VOL_SURGE}×, BOS, liq>${MIN_LIQ_USD:,}")
    print("=" * 65)

    print("\nFetching trending pools (5 pages) ...")
    pools = fetch_trending_pools(pages=5)
    print(f"Found {len(pools)} unique pools\n")

    all_trades: List[dict] = []
    bal     = [STARTING_BALANCE]
    tested  = skipped = 0

    for pool in pools:
        attrs   = pool.get('attributes', {})
        pool_id = pool['id'].split('_', 1)[1] if '_' in pool['id'] else pool['id']
        name    = attrs.get('name', '?')
        sym     = name.split(' / ')[0] if ' / ' in name else name
        liq     = float(attrs.get('reserve_in_usd', 0) or 0)

        candles = fetch_hourly(pool_id, limit=720)
        if len(candles) < 24:
            skipped += 1
            continue

        tested += 1
        trades = simulate_token(sym, candles, liq, bal)
        if trades:
            all_trades.extend(trades)
            w = sum(1 for t in trades if t['pnl'] > 0)
            p = sum(t['pnl'] for t in trades)
            print(f"  {sym:14s} | {len(trades):3d} trades | {w}/{len(trades)} W "
                  f"| PnL ${p:+.2f}  ({len(candles)}h data)")

    print_report(all_trades, tested, skipped)


if __name__ == '__main__':
    run_backtest()

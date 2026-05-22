import asyncio, aiohttp

async def test():
    try:
        async with aiohttp.ClientSession() as s:
            url = 'https://public-api.birdeye.so/public/tokenlist?sort_by=v24hUSD&sort_type=desc&offset=0&limit=5'
            async with s.get(url, headers={'X-API-KEY': 'public', 'x-chain': 'solana'}) as r:
                print(f"Status: {r.status}")
                data = await r.json()
                tokens = data.get('data', {}).get('tokens', [])
                print(f"Found {len(tokens)} tokens")
                for t in tokens[:3]:
                    sym = t.get('symbol', '?')
                    vol = t.get('volume24hUSD', 0)
                    liq = t.get('liquidity', 0)
                    ch1 = t.get('priceChange24hPercent', 0)
                    print(f"  {sym}: vol={vol}, liq={liq}, ch24h={ch1}%")
                    print(f"    keys: {list(t.keys())[:15]}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())

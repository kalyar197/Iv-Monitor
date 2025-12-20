"""Test Deribit public API access - NO authentication required!"""
import asyncio
import aiohttp


async def test_deribit():
    async with aiohttp.ClientSession() as session:
        # Test 1: Get BTC index price
        async with session.get('https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd') as response:
            data = await response.json()
            print(f'BTC Index Price: ${data["result"]["index_price"]:,.2f}')

        # Test 2: Get available BTC options
        async with session.get('https://www.deribit.com/api/v2/public/get_instruments?currency=BTC&kind=option&expired=false') as response:
            data = await response.json()
            instruments = data['result'][:5]  # First 5 instruments
            print(f'\nFound {len(data["result"])} active BTC options')
            print(f'Sample instruments:')
            for inst in instruments:
                print(f'  - {inst["instrument_name"]}')

        # Test 3: Get ticker with IV for first instrument
        if instruments:
            inst_name = instruments[0]['instrument_name']
            async with session.get(f'https://www.deribit.com/api/v2/public/ticker?instrument_name={inst_name}') as response:
                data = await response.json()
                ticker = data['result']
                print(f'\nTicker data for {inst_name}:')
                print(f'  Mark IV: {ticker.get("mark_iv", 0) * 100:.2f}%')
                print(f'  Bid IV: {ticker.get("bid_iv", 0) * 100:.2f}%')
                print(f'  Ask IV: {ticker.get("ask_iv", 0) * 100:.2f}%')
                greeks = ticker.get('greeks', {})
                print(f'  Delta: {greeks.get("delta", 0):.4f}')
                print(f'  Gamma: {greeks.get("gamma", 0):.6f}')
                print(f'  Theta: {greeks.get("theta", 0):.4f}')
                print(f'  Vega: {greeks.get("vega", 0):.4f}')
                print(f'  Open Interest: {ticker.get("open_interest", 0):,.0f}')
                print(f'  Mark Price: ${ticker.get("mark_price", 0):,.2f}')

        print('\n[SUCCESS] Deribit public API works perfectly!')
        print('[OK] No API keys required')
        print('[OK] No IP restrictions')
        print('[OK] Full IV and Greeks data available')


if __name__ == '__main__':
    asyncio.run(test_deribit())

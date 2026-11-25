"""Broker-style pair catalog built from live Deriv instruments with weekend/session filtering."""
import time
from fastapi import APIRouter, Query
from market_config import FRESHNESS
from market_routes import instrument_is_open
from pairs_config import demo_payout_percent, market_category

QX_SOURCE = 'market-qx-observer-v2'


def pairs_router(db):
    router = APIRouter(prefix='/api/v1', tags=['Pairs'])

    @router.get('/pairs')
    async def pairs(mode: str = Query('ALL', pattern='^(ALL|FOREX|BINARY)$'), include_closed: bool = False):
        query = {'$or': [{'source': 'deriv', 'marketDataEligible': True}, {'source': QX_SOURCE, 'is_otc': True}]}
        rows = await db.market_instruments.find(query, {'_id': 0}).to_list(2000)
        now = time.time()
        items = []
        for row in rows:
            quotex = row['source'] == QX_SOURCE
            modes = ['BINARY'] if quotex else ['FOREX', 'BINARY']
            if mode != 'ALL' and mode not in modes:
                continue
            fresh = row.get('latestEpoch') is not None and now - float(row['latestEpoch']) <= FRESHNESS
            is_open = (fresh and row.get('available') is not False) if quotex else instrument_is_open(row)
            if not include_closed and not is_open:
                continue
            items.append({
                'symbol': row['symbol'], 'name': row.get('label') or row['symbol'], 'source': row['source'],
                'broker': 'Quotex' if quotex else 'Deriv', 'brokerSymbol': row.get('brokerSymbol'),
                'market': row.get('market'), 'submarket': row.get('submarket'),
                'category': 'otc' if quotex else market_category(row.get('market')), 'modes': modes,
                'isOpen': is_open, 'fresh': fresh, 'signalEligible': bool(row.get('signalEligible')),
                'latestPrice': row.get('latestPrice'), 'latestEpoch': row.get('latestEpoch'),
                'payoutPercent': row.get('payoutPercent') if quotex else demo_payout_percent(row.get('market')),
            })
        order = {'forex': 0, 'binary': 1, 'crypto': 2, 'commodities': 3, 'otc': 0}
        items.sort(key=lambda item: (order.get(item['category'], 4), item['name']))
        forex_open = any(row.get('source') == 'deriv' and row.get('market') == 'forex' and instrument_is_open(row) for row in rows)
        return {'items': items, 'forexSessionOpen': forex_open, 'generatedAt': now}

    return router

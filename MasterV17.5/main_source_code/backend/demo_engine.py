"""Virtual-money demo ledger settled with real Deriv tick prices; never used by live analytical signals."""
import copy
import time
from fastapi import HTTPException
from market_routes import instrument_is_open
from pairs_config import demo_payout_percent

DURATIONS = [5, 15, 30, 60, 120, 300]
ACTIVE = {'OPEN', 'PENDING'}
LIVE_PRICE_MAX_AGE = 10
NO_DATA_REFUND_SECONDS = 120


class DerivPriceBook:
    """Reads stored Deriv ticks so entries and expiries use real provider prices."""

    def __init__(self, db):
        self.db = db

    async def instrument(self, symbol):
        return await self.db.market_instruments.find_one(
            {'$or': [{'source': 'deriv', 'symbol': symbol, 'marketDataEligible': True},
                     {'source': 'market-qx-observer-v2', 'symbol': symbol}]}, {'_id': 0},
        )

    async def live_price(self, symbol):
        row = await self.instrument(symbol)
        if not row or row.get('latestEpoch') is None or time.time() - float(row['latestEpoch']) > LIVE_PRICE_MAX_AGE:
            return None
        return float(row['latestPrice'])

    async def price_at(self, symbol, epoch_ms):
        """Last tick at or before the timestamp, only once the feed has moved past it."""
        at = epoch_ms / 1000
        row = await self.instrument(symbol)
        if not row or float(row.get('latestEpoch') or 0) < at:
            return None
        tick = await self.db.market_ticks.find_one(
            {'source': row['source'], 'symbol': symbol, 'epoch': {'$lte': at, '$gte': at - 30}},
            {'_id': 0, 'price': 1}, sort=[('epoch', -1)],
        )
        return float(tick['price']) if tick else None


async def settle(account, now, prices):
    for trade in account['trades']:
        if trade['status'] == 'PENDING' and now >= trade['startsAt']:
            entry = await prices.price_at(trade['pairId'], trade['startsAt'])
            if entry is not None:
                trade.update(status='OPEN', entryPrice=entry)
            elif now - trade['startsAt'] > NO_DATA_REFUND_SECONDS * 1000:
                trade.update(status='CANCELLED', creditCents=trade['stakeCents'])
                account['balanceCents'] += trade['stakeCents']
        if trade['status'] == 'OPEN' and now >= trade['expiresAt']:
            exit_price = await prices.price_at(trade['pairId'], trade['expiresAt'])
            if exit_price is None:
                if now - trade['expiresAt'] > NO_DATA_REFUND_SECONDS * 1000:
                    trade.update(status='TIED', exitPrice=trade['entryPrice'], creditCents=trade['stakeCents'])
                    account['balanceCents'] += trade['stakeCents']
                continue
            tied = exit_price == trade['entryPrice']
            won = exit_price > trade['entryPrice'] if trade['direction'] == 'UP' else exit_price < trade['entryPrice']
            credit = trade['stakeCents'] if tied else trade['stakeCents'] + int(trade['stakeCents'] * trade['payoutPercent'] / 100 + .5) if won else 0
            trade.update(exitPrice=exit_price, creditCents=credit, status='TIED' if tied else 'WON' if won else 'LOST')
            account['balanceCents'] += credit


async def apply_action(original, prices, action=None):
    account = copy.deepcopy(original)
    now = int(time.time() * 1000)
    await settle(account, now, prices)
    if action is None:
        return account, ''
    request_id = action['requestId']
    if request_id in account['receipts']:
        return account, 'Request already saved.'
    kind = action['kind']
    if kind in {'deposit', 'withdraw'}:
        amount = action['cents']
        if amount is None or not 100 <= amount <= 100_000_000:
            raise HTTPException(422, 'Enter a demo amount between $1 and $1,000,000.')
        if kind == 'withdraw' and amount > account['balanceCents']:
            raise HTTPException(409, 'Insufficient available demo balance.')
        if kind == 'deposit' and account['balanceCents'] + amount > 1_000_000_000:
            raise HTTPException(422, 'Maximum demo balance is $10,000,000.')
        account['balanceCents'] += amount if kind == 'deposit' else -amount
        message = 'Demo funds added. No real payment was made.' if kind == 'deposit' else 'Demo funds removed. No real withdrawal was made.'
    elif kind == 'reset':
        for trade in account['trades']:
            if trade['status'] in ACTIVE:
                trade['status'] = 'CANCELLED'
        account['archive'].extend(account['trades'])
        account.update(balanceCents=1_000_000, trades=[])
        message = 'Demo account reset to $10,000.00. Previous history archived.'
    else:
        pair, stake, duration = action['pairId'], action['stakeCents'], action['duration']
        instrument = await prices.instrument(pair) if pair else None
        if instrument is None or duration not in DURATIONS or action['direction'] not in {'UP', 'DOWN'}:
            raise HTTPException(422, 'Choose a valid live market, direction and expiry time.')
        if not instrument_is_open(instrument):
            raise HTTPException(409, 'Market is closed right now (weekend / session closed).')
        if stake is None or stake < 100:
            raise HTTPException(422, 'Minimum demo investment is $1.00.')
        if stake > account['balanceCents']:
            raise HTTPException(409, 'Insufficient demo balance for this investment.')
        if sum(t['status'] in ACTIVE for t in account['trades']) >= 20:
            raise HTTPException(409, 'Maximum 20 active demo trades. Wait for an expiry.')
        entry = None
        if not action['pending']:
            entry = await prices.live_price(pair)
            if entry is None:
                raise HTTPException(409, 'No live Deriv price for this market yet. Try again in a moment.')
        starts_at = (now // 60000 + 1) * 60000 if action['pending'] else now
        trade = dict(id=request_id, pairId=pair, label=instrument.get('label') or pair, direction=action['direction'], stakeCents=stake,
                     payoutPercent=instrument.get('payoutPercent') or demo_payout_percent(instrument.get('market')), duration=duration, createdAt=now,
                     startsAt=starts_at, expiresAt=starts_at + duration * 1000,
                     entryPrice=entry, exitPrice=None,
                     status='PENDING' if action['pending'] else 'OPEN', creditCents=0)
        account['trades'].insert(0, trade)
        account['balanceCents'] -= stake
        message = f"{action['direction']} demo trade {'scheduled for the next minute' if action['pending'] else 'opened at live Deriv price'}."
    account['receipts'] = (account['receipts'] + [request_id])[-2000:]
    return account, message

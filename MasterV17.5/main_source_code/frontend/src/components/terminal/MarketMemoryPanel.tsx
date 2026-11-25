import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiGet } from '@/lib/api';

interface MemoryEvent { source: string; symbol: string; timeframe: string; type: string; epoch: number; price?: number; strength?: number; note?: string }
interface AlgoRow { source: string; symbol: string; timeframe: string; algorithm: string; wins?: number; losses?: number; winRate: number | null }

const TYPES = ['ALL', 'BULL_TRAP', 'BEAR_TRAP', 'GAP_UP', 'GAP_DOWN', 'STOP_HUNT'] as const;
const TYPE_COLOR: Record<string, string> = { BULL_TRAP: '#f6465d', BEAR_TRAP: '#16c784', GAP_UP: '#3b9cff', GAP_DOWN: '#ff9f43', STOP_HUNT: '#f5c451' };
const when = (epoch: number) => new Date(epoch * 1000).toISOString().replace('T', ' ').slice(0, 19);
const label = (value: string) => value.replaceAll('_', ' ');
const rateColor = (rate: number | null) => rate === null ? '#8896ab' : rate >= 60 ? '#16c784' : rate >= 50 ? '#f5c451' : '#f6465d';

function MemoryEvents() {
  const [type, setType] = useState<(typeof TYPES)[number]>('ALL');
  const query = useQuery({ queryKey: ['memory-events', type], queryFn: () => apiGet<{ items: MemoryEvent[] }>(`/v1/memory/events?limit=100${type === 'ALL' ? '' : `&type=${type}`}`), refetchInterval: 15000 });
  const rows = query.data?.items ?? [];
  return <section className="workspace-card" data-testid="memory-events-panel">
    <header>
      <div><p>MARKET MEMORY</p><h2>Trap &amp; gap events</h2></div>
      <div className="workspace-segment" role="group" aria-label="Event type">{TYPES.map(t => <button key={t} type="button" data-testid={`memory-event-filter-${t.toLowerCase()}`} aria-pressed={type === t} className={type === t ? 'selected' : ''} onClick={() => setType(t)}>{label(t)}</button>)}</div>
    </header>
    <div className="workspace-table-wrap memory-table-scroll"><table data-testid="memory-events-table"><thead><tr><th>Time (UTC)</th><th>Pair</th><th>TF</th><th>Event</th><th>Price</th><th>Strength</th><th>Note</th></tr></thead>
      <tbody>{rows.map((row, i) => <tr key={`${row.symbol}-${row.timeframe}-${row.epoch}-${row.type}`} data-testid={`memory-event-row-${i}`}>
        <td>{when(row.epoch)}</td><td>{row.symbol}</td><td>{row.timeframe}</td>
        <td><b data-testid={`memory-event-type-${i}`} style={{ color: TYPE_COLOR[row.type] ?? '#c9d5ea' }}>{label(row.type)}</b></td>
        <td>{row.price ?? '—'}</td><td>{row.strength === undefined ? '—' : row.strength.toFixed(2)}</td><td>{row.note ?? '—'}</td>
      </tr>)}</tbody></table>
      {!rows.length && <p className="workspace-empty" data-testid="memory-events-empty">{query.isLoading ? 'Loading…' : query.isError ? 'Connection unavailable.' : 'No events recorded yet.'}</p>}
    </div>
  </section>;
}

function AlgorithmWinRates() {
  const query = useQuery({ queryKey: ['memory-algorithms'], queryFn: () => apiGet<{ items: AlgoRow[] }>('/v1/memory/algorithms'), refetchInterval: 15000 });
  const rows = query.data?.items ?? [];
  return <section className="workspace-card" data-testid="algorithm-winrate-panel">
    <header><div><p>ALGORITHM MEMORY</p><h2>Win-rate per algorithm (settled signals)</h2></div></header>
    <div className="workspace-table-wrap memory-table-scroll"><table data-testid="algorithm-winrate-table"><thead><tr><th>Algorithm</th><th>Pair</th><th>TF</th><th>Wins</th><th>Losses</th><th>Trades</th><th>Win-rate</th></tr></thead>
      <tbody>{rows.map((row, i) => { const wins = row.wins ?? 0, losses = row.losses ?? 0; return <tr key={`${row.algorithm}-${row.symbol}-${row.timeframe}`} data-testid={`algorithm-row-${i}`}>
        <td>{label(row.algorithm)}</td><td>{row.symbol}</td><td>{row.timeframe}</td><td>{wins}</td><td>{losses}</td><td>{wins + losses}</td>
        <td><b data-testid={`algorithm-winrate-${i}`} style={{ color: rateColor(row.winRate) }}>{row.winRate === null ? '—' : `${row.winRate}%`}</b></td>
      </tr>; })}</tbody></table>
      {!rows.length && <p className="workspace-empty" data-testid="algorithm-winrate-empty">{query.isLoading ? 'Loading…' : query.isError ? 'Connection unavailable.' : 'No settled signals yet.'}</p>}
    </div>
  </section>;
}

export default function MarketMemoryPanel() {
  return <div data-testid="market-memory-panel"><MemoryEvents /><AlgorithmWinRates /></div>;
}

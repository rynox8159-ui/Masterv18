import { Link, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Download, RefreshCw, Activity, BarChart3, BrainCircuit, GitBranch, Settings2, ShieldCheck, Terminal, Wifi } from 'lucide-react';
import Brand from '@/components/terminal/Brand';
import { Button } from '@/components/ui/button';
import { apiGet } from '@/lib/api';
import PostgresAnalytics from '@/components/terminal/PostgresAnalytics';
import MirrorSettings from '@/components/terminal/MirrorSettings';
import AlertPreferences from '@/components/terminal/AlertPreferences';
import SignalTracker from '@/components/terminal/SignalTracker';
import SignalSettings from '@/components/terminal/SignalSettings';
import QuotexCheck from '@/components/terminal/QuotexCheck';
import MarketMemoryPanel from '@/components/terminal/MarketMemoryPanel';
import TrainingPanel from '@/components/terminal/TrainingPanel';
import SnifferProof from '@/components/terminal/SnifferProof';
import AccuracyReport from '@/components/terminal/AccuracyReport';
import './workspace.css';

const views = {
  signals: { title: 'Signal history', subtitle: 'LIVE FUTURE SIGNALS · ANALYTICAL DECISIONS', endpoint: '/v1/signals/history?limit=1000', icon: Activity },
  analytics: { title: 'Market analytics', subtitle: 'TOP 15 · DATA QUALITY', endpoint: '/v1/top-pairs', icon: BarChart3 },
  flow: { title: 'System flow', subtitle: 'MODULE STATUS', endpoint: '/v1/modules', icon: GitBranch },
  logs: { title: 'Event stream', subtitle: 'BACKEND ACTIVITY', endpoint: '/v1/events', icon: Terminal },
  training: { title: 'Model training', subtitle: 'ML · DL · DS PIPELINE · ALL PAIRS', endpoint: '/v1/ml/model-history?limit=80', icon: BrainCircuit },
  accuracy: { title: 'Accuracy proof', subtitle: 'WALK-FORWARD · OUT-OF-SAMPLE · PAYOUT-AWARE PROMOTION', endpoint: '/v1/promotion/status', icon: ShieldCheck },
  sniffer: { title: 'Quotex sniffer', subtitle: 'WS SNIFFER · PLAYWRIGHT PROOF', endpoint: '/v1/observation/proof', icon: Wifi },
  settings: { title: 'Connections', subtitle: 'RUNTIME CONFIGURATION', endpoint: '/v1/runtime', icon: Settings2 },
};
type Row = Record<string, unknown>;
const display = (value: unknown, field = '') => value === undefined || value === null ? '—' : (field.endsWith('Epoch') || field === 'generatedAt') && typeof value === 'number' ? new Date(value * 1000).toISOString().replace('T', ' ').slice(0, 19) : typeof value === 'object' ? JSON.stringify(value) : String(value).replaceAll('_', ' ');

export default function Workspace() {
  const location = useLocation();
  const key = location.pathname.slice(1) as keyof typeof views;
  const view = views[key] || views.signals;
  const query = useQuery({ queryKey: ['workspace', key], queryFn: () => apiGet<Row>(view.endpoint), refetchInterval: 10000 });
  const data = query.data;
  const rows = (key === 'settings' ? data?.providers : key === 'sniffer' ? data?.lastTicks : data?.items) as Row[] | undefined;
  const customView = key === 'training' || key === 'sniffer' || key === 'accuracy';
  const columns: [string, string][] = key === 'signals' ? [['label', 'Pair'], ['source', 'Source'], ['timeframe', 'Timeframe'], ['entryEpoch', 'Entry (UTC)'], ['direction', 'Direction'], ['confidence', 'Setup score'], ['status', 'Result'], ['entryOpen', 'Open'], ['exitClose', 'Close'], ['edgeGate', 'Edge gate'], ['validationTier', 'Validation'], ['generatedAt', 'Generated (UTC)']]
    : key === 'analytics' ? [['symbol', 'Pair'], ['source', 'Source'], ['quality', 'Quality score'], ['candleCount', 'Candles'], ['signal', 'Decision'], ['qualification', 'Qualification']]
    : key === 'flow' ? [['name', 'Module'], ['state', 'Status'], ['engines', 'Engines']]
    : key === 'logs' ? [['time', 'Time (UTC)'], ['level', 'Level'], ['source', 'Source'], ['message', 'Event']]
    : key === 'training' ? [['symbol', 'Pair'], ['timeframe', 'TF'], ['status', 'Status'], ['promotionTier', 'Tier']]
    : key === 'sniffer' ? [['symbol', 'Pair'], ['price', 'Price'], ['at', 'Broker time']]
    : [['source', 'Provider'], ['state', 'Status'], ['acceptedCount', 'Active streams'], ['rejectedCount', 'Rejected'], ['error', 'Connection error']];
  const exportRows = () => {
    const csv = [columns.map(([, title]) => title), ...(rows || []).map(row => columns.map(([field]) => display(row[field] ?? (field === 'edgeGate' ? 'LEGACY' : undefined), field)))].map(row => row.map(cell => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `master-candle-${key}.csv`; link.click(); URL.revokeObjectURL(url);
  };
  return <div className="terminal-shell workspace-screen" data-testid="workspace-screen">
    <header className="terminal-header"><Brand /><span className="flex-1" /><Link className="workspace-back" to="/" data-testid="workspace-back"><ArrowLeft size={15} /> Terminal</Link></header>
    <div className="workspace-layout">
      <nav className="workspace-nav" data-testid="workspace-navigation">{Object.entries(views).map(([id, item]) => { const Icon = item.icon; return <Link data-testid={`workspace-nav-${id}`} className={key === id ? 'selected' : ''} key={id} to={`/${id}`}><Icon size={18} /><span>{item.title}</span></Link>; })}</nav>
      <main className="workspace-content">
        <div className="workspace-heading"><div><p data-testid="workspace-subtitle">{view.subtitle}</p><h1 data-testid="workspace-title">{view.title}</h1></div><div className="workspace-actions"><Button data-testid="workspace-refresh" variant="ghost" disabled={query.isFetching} onClick={() => void query.refetch()} aria-label="Refresh data"><RefreshCw size={16} /></Button><Button data-testid="workspace-export" variant="ghost" disabled={!rows?.length} onClick={exportRows}><Download size={16} /> CSV</Button></div></div>
        {key === 'settings' && <div className="workspace-summary" data-testid="workspace-runtime-summary"><span>Database <b data-testid="workspace-database">{display(data?.database)}</b></span><span>Received ticks <b data-testid="workspace-ticks">{display(data?.ticksReceived)}</b></span><span>Analysis cycles <b data-testid="workspace-cycles">{display(data?.analysisCycles)}</b></span><a data-testid="observer-download-link" className="workspace-download" href={`${import.meta.env.VITE_BACKEND_URL}/api/v1/observer/download`}><Download size={16} /> Observer extension</a></div>}
        {key === 'signals' && <><SignalTracker /><QuotexCheck /><h2 className="workspace-section-title" data-testid="legacy-history-title">Signal history (all fields · CSV)</h2></>}
        {key === 'training' && <TrainingPanel />}
        {key === 'accuracy' && <AccuracyReport />}
        {key === 'sniffer' && <SnifferProof />}
        {customView ? null : query.isLoading ? <p data-testid="workspace-loading" className="workspace-empty">Loading…</p> : query.isError ? <p role="alert" data-testid="workspace-error" className="workspace-empty">Connection unavailable. Please refresh.</p> : <div className="workspace-table-wrap"><table data-testid="workspace-table"><thead><tr>{columns.map(([field, title]) => <th key={field} data-testid={`workspace-heading-${field}`}>{title}</th>)}</tr></thead><tbody>{rows?.map((row, index) => <tr key={index} data-testid={`workspace-row-${index}`}>{columns.map(([field]) => <td data-testid={`workspace-cell-${index}-${field}`} key={field}>{display(row[field] ?? (field === 'edgeGate' ? 'LEGACY' : undefined), field)}</td>)}</tr>)}</tbody></table>{!rows?.length && <p data-testid="workspace-empty" className="workspace-empty">{key === 'analytics' ? 'No fresh instruments meet the quality threshold.' : 'No records available yet.'}</p>}</div>}
        {(key === 'signals' || key === 'analytics') && <MarketMemoryPanel />}
        {key === 'analytics' && <PostgresAnalytics />}
        {key === 'settings' && <><SignalSettings /><MirrorSettings /><AlertPreferences /></>}
        <footer className="terminal-footer"><span data-testid="workspace-disclaimer">MASTER CANDLE V17.3 · ANALYTICAL SIGNALS · NO REAL TRADES</span><span data-testid="workspace-row-count">{rows?.length || 0} records</span></footer>
      </main>
    </div>
  </div>;
}
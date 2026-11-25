import asyncio
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(BACKEND_DIR / '.env')

from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone
import uuid
from market_config import ORIGINS, DERIV_ENABLED
from market_store import MarketStore
from deriv_service import DerivService
from market_analysis import AnalysisService
from market_routes import market_router
from observation_routes import observation_router
from extension_download import router as download_router
from project_export import project_router
from training_knowledge import TrainingKnowledge, training_router
from loop_monitor import loop_router, monitor
from remote_browser import RemoteBrowser, remote_browser_router
from demo_routes import demo_router
from postgres_service import PostgresService
from postgres_routes import postgres_router
from signal_service import SignalService
from accumulator_signals import AccumulatorSignalService
from signal_routes import signal_router
from master_agent import MasterAgent
from market_gatekeeper import RedisMarketCache, BaseAgent, BaseAgentPool, MarketDataRouter
from market_agent_registry import MarketAgentRegistry, agents_router
from telemetry_routes import telemetry_router
from provider_control import ProviderController, provider_control_router
from quotex_proxy import quotex_proxy_router
from ml.ml_model_adapters import Step8ModelRunner, ml_router
from dl.deep_model_service import DeepModelService
from auto_trade.dashboard_engine import AutoTradeEngine
from autotrade_routes import autotrade_router
from ds.forecasting.time_series_predictor import TimeSeriesPredictor
from ds.ensemble_fusion import EnsembleFusion
from ds.forecasting.signal_confluence import SignalConfluenceEngine
from ds.forecasting.risk_manager import RiskManager
from market_memory import MarketMemoryLayer, memory_router
from pairs_routes import pairs_router
from api_key_auth import api_key_middleware, auth_router, configured_credential
from training_orchestrator import TrainingOrchestrator, training_pipeline_router
from feature_store import FeatureStore, feature_router
from observation_routes import ObserverProof
from promotion_gate import PromotionGate, promotion_router
import training_workers
import server_clock
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

mongo_max_pool_size = max(20, int(os.environ.get('MONGO_MAX_POOL_SIZE', '20')))
mongo_min_pool_size = min(
    mongo_max_pool_size,
    max(0, int(os.environ.get('MONGO_MIN_POOL_SIZE', '1'))),
)
client = AsyncIOMotorClient(
    os.environ.get('MONGO_URL', 'mongodb://127.0.0.1:27017'),
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    waitQueueTimeoutMS=5000,
    retryWrites=True,
    minPoolSize=mongo_min_pool_size,
    maxPoolSize=mongo_max_pool_size,
)
db = client[os.environ.get('DB_NAME', 'master_candel')]
store = MarketStore(db)
training_knowledge = TrainingKnowledge(db)
remote_browser = RemoteBrowser()
market_cache = RedisMarketCache()
base_agent = BaseAgent(store)
agent_pool = BaseAgentPool(base_agent, concurrency=max(1, int(os.environ.get('MAX_AGENT_CONCURRENCY', '5'))))
market_data_router = MarketDataRouter(store, market_cache, agent_pool)
deriv = DerivService(store, market_cache, agent_pool)
provider_controller = ProviderController(deriv)
analysis = AnalysisService(store)
postgres = PostgresService()
store.mirror = postgres
market_agent_registry = MarketAgentRegistry(max_agents=500)
signals = SignalService(store, deriv if DERIV_ENABLED else None, market_registry=market_agent_registry)
accumulator_signals = AccumulatorSignalService(store, deriv if DERIV_ENABLED else None)
signals.training_knowledge = training_knowledge
auto_trade_engine = AutoTradeEngine(db)
auto_trade_engine.signal_service = signals
auto_trade_engine.accumulator_signal_service = accumulator_signals
master_agent = MasterAgent(db)
signals.master_agent = master_agent
signals.postgres_service = postgres
step8_runner = Step8ModelRunner(max_agents=500)
deep_models = DeepModelService(db, deriv)
signals.model_runner = step8_runner
signals.deep_model_service = deep_models

signal_time_series_predictor = TimeSeriesPredictor(confidence_threshold=0.68)
signal_ensemble_fusion = EnsembleFusion()
signal_confluence = SignalConfluenceEngine(min_agree=3, max_oppose=1, confidence_threshold=0.72)
signal_risk_manager = RiskManager(min_confidence=0.60)

signals.time_series_predictor = signal_time_series_predictor
signals.ensemble_fusion = signal_ensemble_fusion
signals.confluence_engine = signal_confluence
signals.risk_manager = signal_risk_manager
memory_layer = MarketMemoryLayer(db)
signals.memory_layer = memory_layer
training_pipeline = TrainingOrchestrator(db, deriv, signals, deep_models, step8_runner, timeframes=[tf.strip() for tf in os.environ.get('TRAIN_ALL_TIMEFRAMES', '1m,5m').split(',') if tf.strip()])
feature_store = FeatureStore(db, deriv)
training_pipeline.feature_store = feature_store
observer_proof = ObserverProof()
promotion_gate = PromotionGate(db, store, deriv)
signals.promotion_gate = promotion_gate
auto_trade_engine.deriv_feed = deriv
from brain import SignalBrain
from brain_routes import brain_router
brain = SignalBrain(db, store, deriv)
deriv.tick_listeners.append(brain.on_tick)
store.brain = brain
auto_trade_engine.brain = brain
store.flow_context = {'registry': market_agent_registry, 'runner': step8_runner, 'deep': deep_models, 'signals': signals, 'autotrade': auto_trade_engine}
app_state = {'signal_runtime': {'predictor': signal_time_series_predictor, 'fusion': signal_ensemble_fusion, 'confluence': signal_confluence, 'risk_manager': signal_risk_manager}}


@asynccontextmanager
async def lifespan(app):
    server_clock.install_shutdown_signals()
    await store.initialize()
    await auto_trade_engine.load()
    await memory_layer.initialize()
    await training_knowledge.initialize()
    await feature_store.initialize()
    await promotion_gate.initialize()
    logging.getLogger('naqso').info('Backend API key active (%s)', 'BACKEND_API_KEY from .env' if os.environ.get('BACKEND_API_KEY') else 'built-in key shared with frontend')
    await deep_models.initialize()
    await master_agent.initialize()
    await market_cache.connect()
    await store.event('INFO', 'Main Server Core', 'MongoDB connected · analytical ingestion ready')
    saved = await db.runtime_settings.find_one({'id': 'postgres_mirror'}, {'_id': 0, 'id': 0})
    if saved:
        postgres.update_settings(**saved)
    await postgres.connect()
    vector_status = await postgres.ensure_vector_tables()
    if postgres.enabled:
        await store.event('INFO' if postgres.state == 'CONNECTED' else 'WARN', 'Main Server Core', f'PostgreSQL {postgres.state}')
    if vector_status.get('status') not in {'READY', 'DISABLED'}:
        await store.event('WARN', 'PostgreSQL Vector Store', f"Vector search {vector_status['status']}; JSONB fallback active")
    await signals.load_settings()
    signals.start()
    await store.event('INFO', 'Signal Engine', f"Confluence engine armed · threshold {signals.settings['threshold']}% · {', '.join(signals.settings['timeframes'])}")
    tasks = [asyncio.create_task(analysis.run()), asyncio.create_task(monitor.run()), asyncio.create_task(feature_store.run()), asyncio.create_task(training_pipeline.daily_schedule()), asyncio.create_task(training_pipeline.incremental_schedule()), asyncio.create_task(promotion_gate.schedule())]
    if DERIV_ENABLED:
        await provider_controller.start('deriv')
        accumulator_signals.start()
    if os.environ.get('QX_PROXY', '').strip() and os.environ.get('QX_OBSERVER_AUTOSTART', 'false').lower() == 'true':
        try:
            await provider_controller.start('playwright_observer')
        except RuntimeError as exc:
            logging.getLogger('naqso').warning('Quotex observer autostart failed: %s', exc)
    yield

    async def bounded(label, coro, timeout=10):
        try:
            await asyncio.wait_for(coro, timeout)
        except Exception as exc:  # noqa: BLE001 - shutdown must never hang (hot reload / Ctrl+C)
            logging.getLogger('naqso').warning('Shutdown step %s: %s', label, type(exc).__name__)

    for task in tasks:
        task.cancel()
    await bounded('background tasks', asyncio.gather(*tasks, return_exceptions=True), 5)
    await bounded('signals', signals.stop(), 10)
    await bounded('accumulator signals', accumulator_signals.stop(), 10)
    await bounded('providers', provider_controller.stop_all(), 10)
    await bounded('training pipeline', training_pipeline.stop(), 5)
    await bounded('promotion gate', promotion_gate.stop(), 5)
    training_workers.shutdown()
    await bounded('auto trade', auto_trade_engine.shutdown(), 20)
    await bounded('analysis', analysis.stop_worker(), 5)
    await bounded('cache', market_cache.close(), 3)
    await bounded('postgres', postgres.close(), 3)
    client.close()


app = FastAPI(title='NAQSO / Master Candle market backend', lifespan=lifespan)
app.middleware('http')(api_key_middleware)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True, allow_methods=['GET', 'POST', 'OPTIONS'], allow_headers=['Content-Type', 'X-API-Key', 'X-Market-QX-Key', 'X-Provider-Control-Key', 'X-Autotrade-Real-Key'])
app.include_router(auth_router())
app.include_router(memory_router(memory_layer))
app.include_router(pairs_router(db))
app.include_router(market_router(store, deriv, analysis, agent_pool))
app.include_router(observation_router(store, postgres, market_data_router, observer_proof))
app.include_router(download_router)
app.include_router(project_router(db))
app.include_router(training_router(training_knowledge))
app.include_router(loop_router())
app.include_router(remote_browser_router(remote_browser))
app.include_router(demo_router(db))
app.include_router(postgres_router(db, postgres))
app.include_router(signal_router(store, deriv if DERIV_ENABLED else None, signals, accumulator_signals))
app.include_router(ml_router(step8_runner, deep_models, signals))
app.include_router(telemetry_router(store, deriv if DERIV_ENABLED else None, market_data_router, signals, postgres, deep_models))
app.include_router(quotex_proxy_router(provider_controller))
app.include_router(provider_control_router(provider_controller))
app.include_router(autotrade_router(auto_trade_engine))
app.include_router(brain_router(brain, deriv if DERIV_ENABLED else None))
app.include_router(training_pipeline_router(training_pipeline))
app.include_router(feature_router(feature_store))
app.include_router(promotion_router(promotion_gate))
app.include_router(agents_router(market_agent_registry, db))


@app.get('/api/health')
async def health():
    await db.command('ping')
    return {'status': 'ok', 'database': 'connected', 'authConfigured': bool(configured_credential()), 'authRequired': True, 'postgres': await postgres.ping(), 'marketData': market_data_router.status(), 'executionEnabled': True, 'deriv': {'state': deriv.state, 'lastTick': deriv.last_tick, 'error': deriv.last_error, 'pairs': len(deriv.accepted)}, 'sniffer': observer_proof.snapshot()['state'], 'promotedPairs': len(__import__('promotion_gate').PROMOTED), 'version': 'MasterV17.3'}


@app.get('/api/')
async def root():
    return {'message': 'Master Candle analytical backend'}


class StatusCheck(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


@app.post('/api/status', response_model=StatusCheck)
async def create_status_check(body: StatusCheckCreate):
    result = StatusCheck(client_name=body.client_name)
    await db.status_checks.insert_one(result.model_dump(mode='json'))
    return result


@app.get('/api/status', response_model=list[StatusCheck])
async def get_status_checks():
    return await db.status_checks.find({}, {'_id': 0}).limit(1000).to_list(1000)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host=os.environ.get('BACKEND_HOST', '127.0.0.1'), port=int(os.environ.get('BACKEND_PORT', '7007')))
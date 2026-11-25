"""500-agent registry and market tracking foundation for Step 1.

This module defines a scalable tracking layer that keeps a dedicated agent per
market instrument/timeframe pair and emits a normalized feature bundle for later
ML stages. The goal is to keep the agent model light, deterministic, and easy
for LSTM/Transformer/XGBoost/RandomForest/MARL orchestration to consume.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
import time


@dataclass
class AgentState:
    """Live subscription state for one asset/timeframe agent."""
    agent_id: str
    source: str
    symbol: str
    timeframe: str
    created_at: float
    last_seen: Optional[float] = None
    status: str = "ACTIVE"
    market_snapshot: Dict[str, Any] = field(default_factory=dict)
    feature_summary: Dict[str, Any] = field(default_factory=dict)
    behaviour_summary: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "source": self.source,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "status": self.status,
            "market_snapshot": self.market_snapshot,
            "feature_summary": self.feature_summary,
            "behaviour_summary": self.behaviour_summary,
            "metadata": self.metadata,
        }


class MarketAgent:
    """Single instrument tracker. Each agent owns a source/symbol/timeframe."""

    def __init__(self, agent_id: str, source: str, symbol: str, timeframe: str, metadata: Optional[Dict[str, Any]] = None):
        self.agent_id = agent_id
        self.source = source
        self.symbol = symbol
        self.timeframe = timeframe
        self.metadata = metadata or {}
        self.state = AgentState(
            agent_id=agent_id,
            source=source,
            symbol=symbol,
            timeframe=timeframe,
            created_at=time.time(),
            metadata=self.metadata,
        )

    def ingest_snapshot(self, market_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        self.state.last_seen = float(market_snapshot.get("timestamp") or time.time())
        self.state.market_snapshot = market_snapshot
        self.state.status = "ACTIVE"
        return self.state.to_dict()

    def attach_features(self, feature_summary: Dict[str, Any]) -> Dict[str, Any]:
        self.state.feature_summary = feature_summary or {}
        return self.state.to_dict()

    def attach_behaviour(self, behaviour_summary: Dict[str, Any]) -> Dict[str, Any]:
        self.state.behaviour_summary = behaviour_summary or {}
        return self.state.to_dict()

    def record_assessment(self, assessment: Dict[str, Any], gate: str = '', qualified: bool = False) -> None:
        layer = assessment.get('algorithmLayer') or {}
        self.state.last_seen = time.time()
        self.state.status = 'ACTIVE'
        self.state.metadata['evaluations'] = int(self.state.metadata.get('evaluations', 0)) + 1
        self.state.metadata['qualified'] = int(self.state.metadata.get('qualified', 0)) + (1 if qualified else 0)
        self.state.metadata['last'] = {
            'direction': assessment.get('direction', 'NO_SIGNAL'), 'confidence': float(assessment.get('confidence') or 0),
            'gate': gate, 'qualified': qualified, 'regime': layer.get('regime'), 'edge': layer.get('edge'), 'at': time.time(),
        }

    def snapshot(self) -> Dict[str, Any]:
        return self.state.to_dict()


class MarketAgentRegistry:
    """Registry that keeps up to 500 active market agents."""

    def __init__(self, max_agents: int = 500):
        self.max_agents = max(1, int(max_agents))
        self.agents: Dict[str, MarketAgent] = {}
        self.registration_log: List[Dict[str, Any]] = []

    def _agent_key(self, source: str, symbol: str, timeframe: str) -> str:
        return f"{source}:{symbol}:{timeframe}"

    def register_asset(self, source: str, symbol: str, timeframe: str, metadata: Optional[Dict[str, Any]] = None) -> MarketAgent:
        key = self._agent_key(source, symbol, timeframe)
        if key in self.agents:
            return self.agents[key]
        if len(self.agents) >= self.max_agents:
            # Keep the simplest safe behaviour: reject new entry instead of evicting blindly.
            raise RuntimeError(f"Agent capacity reached ({self.max_agents})")

        agent = MarketAgent(key, source, symbol, timeframe, metadata=metadata)
        self.agents[key] = agent
        self.registration_log.append({
            "timestamp": time.time(),
            "source": source,
            "symbol": symbol,
            "timeframe": timeframe,
            "agent_id": key,
        })
        return agent

    def sync_active(self, markets: Iterable[tuple[str, str, str]]) -> List[MarketAgent]:
        """Retain contexts for active markets and cap the registry at max_agents."""
        identities = list(dict.fromkeys(markets))[:self.max_agents]
        active_keys = {self._agent_key(*identity) for identity in identities}
        for key in list(self.agents):
            if key not in active_keys:
                del self.agents[key]
        return [self.register_asset(*identity) for identity in identities]

    def get_agent(self, source: str, symbol: str, timeframe: str) -> Optional[MarketAgent]:
        return self.agents.get(self._agent_key(source, symbol, timeframe))

    def ingest_market_snapshot(self, source: str, symbol: str, timeframe: str, market_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        agent = self.register_asset(source, symbol, timeframe)
        return agent.ingest_snapshot(market_snapshot)

    def attach_feature_bundle(self, source: str, symbol: str, timeframe: str, feature_summary: Dict[str, Any]) -> Dict[str, Any]:
        agent = self.register_asset(source, symbol, timeframe)
        return agent.attach_features(feature_summary)

    def attach_behaviour(self, source: str, symbol: str, timeframe: str, behaviour_summary: Dict[str, Any]) -> Dict[str, Any]:
        agent = self.register_asset(source, symbol, timeframe)
        return agent.attach_behaviour(behaviour_summary)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "max_agents": self.max_agents,
            "active_agents": len(self.agents),
            "agents": [agent.snapshot() for agent in self.agents.values()],
            "registration_log": self.registration_log[-25:],
        }

    def distribute_feature_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Route a prepared feature bundle only to its matching market agent.

        The payload is deliberately structured to be consumed by the downstream ML
        layers in Step 8 without coupling to a specific framework. Missing identity
        metadata fails closed rather than broadcasting features across markets.
        """
        distribution: Dict[str, Any] = {"drivers": [], "meta": {}, "targets": {}}
        metadata = payload.get("metadata") or {}
        source = metadata.get("source", payload.get("source"))
        symbol = metadata.get("symbol", payload.get("symbol"))
        timeframe = metadata.get("timeframe", payload.get("timeframe"))
        agent = self.get_agent(source, symbol, timeframe) if source and symbol and timeframe else None
        if agent is not None:
            driver = {
                "agent_id": agent.agent_id,
                "source": agent.source,
                "symbol": agent.symbol,
                "timeframe": agent.timeframe,
                "feature_key": payload.get("feature_key"),
                "tabular": payload.get("tabular", {}),
                "sequence": payload.get("sequence", {}),
                "labels": payload.get("labels", {}),
            }
            distribution["drivers"].append(driver)

        distribution["meta"] = {
            "total_agents": len(self.agents),
            "matched_agents": len(distribution["drivers"]),
            "source": source,
            "symbol": symbol,
            "timeframe": timeframe,
            "feature_key": payload.get("feature_key"),
            "framework_ready": bool(distribution["drivers"]),
        }
        distribution["targets"] = {
            "lstm": payload.get("targets", {}).get("lstm", []),
            "transformer": payload.get("targets", {}).get("transformer", []),
            "xgboost": payload.get("targets", {}).get("xgboost", []),
            "random_forest": payload.get("targets", {}).get("random_forest", []),
            "marl": payload.get("targets", {}).get("marl", []),
        }
        return distribution


def build_default_agent_registry(max_agents: int = 500) -> MarketAgentRegistry:
    return MarketAgentRegistry(max_agents=max_agents)


def _edge(agent):
    import algorithm_detector
    profile = algorithm_detector.PROFILES.get((agent.source, agent.symbol, agent.timeframe))
    if not profile:
        return None
    return {k: profile.get(k) for k in ('proven', 'oosHitRate', 'oosSamples', 'oosLowerBound', 'trainPoints', 'validated')}


def agents_router(registry: MarketAgentRegistry, db):
    from fastapi import APIRouter
    router = APIRouter(prefix='/api/v1/agents', tags=['agents'])

    @router.get('/live')
    async def live_agents():
        rows = await db.live_signals.aggregate([
            {'$match': {'status': {'$in': ['WIN', 'LOSS']}}},
            {'$group': {'_id': {'source': '$source', 'symbol': '$symbol', 'timeframe': '$timeframe'},
                        'wins': {'$sum': {'$cond': [{'$eq': ['$status', 'WIN']}, 1, 0]}}, 'total': {'$sum': 1}}},
        ]).to_list(5000)
        record = {f"{r['_id']['source']}:{r['_id']['symbol']}:{r['_id']['timeframe']}": r for r in rows}
        now = time.time()
        items = []
        for agent in list(registry.agents.values()):
            state = agent.state
            stats = record.get(agent.agent_id, {})
            wins, total = int(stats.get('wins', 0)), int(stats.get('total', 0))
            age = None if state.last_seen is None else now - state.last_seen
            status = 'Online' if age is not None and age < 900 else 'Idle' if age is not None else 'Waiting'
            items.append({
                'id': agent.agent_id, 'source': agent.source, 'symbol': agent.symbol, 'timeframe': agent.timeframe,
                'status': status, 'lastSeenAge': None if age is None else round(age, 1),
                'evaluations': state.metadata.get('evaluations', 0), 'qualified': state.metadata.get('qualified', 0),
                'last': state.metadata.get('last'), 'wins': wins, 'losses': total - wins,
                'winRate': round(100.0 * wins / total, 1) if total else None,
                'edge': _edge(agent),
            })
        items.sort(key=lambda x: (x['status'] != 'Online', -(x['winRate'] or 0)))
        settled = sum(i['wins'] + i['losses'] for i in items)
        return {'maxAgents': registry.max_agents, 'active': len(items), 'provenEdge': sum(1 for i in items if (i['edge'] or {}).get('proven')), 'online': sum(i['status'] == 'Online' for i in items),
                'systemWinRate': round(100.0 * sum(i['wins'] for i in items) / settled, 1) if settled else None, 'items': items}

    return router

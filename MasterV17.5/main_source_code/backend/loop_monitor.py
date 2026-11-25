"""Event-loop health sampling for diagnosing delayed feeds and WebSocket heartbeats."""
import asyncio
import collections
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LoopMonitor:
    def __init__(self):
        self.lag_ms = collections.deque(maxlen=240)
        self.hot = collections.Counter()
        self.samples = 0
        self._main_ident = threading.main_thread().ident
        self._sampling = False

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            start = loop.time()
            await asyncio.sleep(0.5)
            self.lag_ms.append(round((loop.time() - start - 0.5) * 1000, 1))

    def _sample(self, seconds):
        try:
            end = time.time() + seconds
            while time.time() < end:
                frame = sys._current_frames().get(self._main_ident)
                if frame is not None:
                    stack = [
                        item for item in traceback.extract_stack(frame)
                        if Path(item.filename).resolve().is_relative_to(PROJECT_ROOT)
                    ]
                    if stack:
                        top = stack[-1]
                        self.hot[f'{Path(top.filename).relative_to(PROJECT_ROOT)}:{top.lineno} {top.name}'] += 1
                        self.samples += 1
                time.sleep(0.01)
        finally:
            self._sampling = False

    def profile(self, seconds=10):
        if self._sampling:
            return False
        self._sampling = True
        self.hot.clear()
        self.samples = 0
        threading.Thread(target=self._sample, args=(seconds,), daemon=True).start()
        return True

    def report(self):
        lags = list(self.lag_ms)
        return {
            'lagMsLast': lags[-1] if lags else None,
            'lagMsMax': max(lags) if lags else None,
            'lagMsAvg': round(sum(lags) / len(lags), 1) if lags else None,
            'samples': self.samples,
            'sampling': self._sampling,
            'hotSpots': [
                {'frame': frame, 'pct': round(100 * count / max(1, self.samples), 1)}
                for frame, count in self.hot.most_common(20)
            ],
        }


monitor = LoopMonitor()


def loop_router():
    router = APIRouter(prefix='/api/v1/system', tags=['system'])

    @router.get('/loop')
    async def loop_report():
        return monitor.report()

    @router.post('/loop/profile')
    async def loop_profile(seconds: int = 10):
        duration = max(2, min(seconds, 60))
        if not monitor.profile(duration):
            return {'started': False, 'reason': 'profile already running'}
        return {'started': True, 'seconds': duration}

    return router

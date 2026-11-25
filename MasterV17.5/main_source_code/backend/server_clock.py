"""Market clock (V16.3): corrects a drifting PC clock against Deriv server time.

Uses the Deriv {"time": 1} API with round-trip compensation (NOT tick epochs, so event-loop
processing lag can never be mistaken for clock drift). Server time is whole seconds, so
offsets under 1.5s are ignored; a median of recent measurements smooths outliers.
"""
import statistics
import time
from collections import deque

_measurements = deque(maxlen=7)
_offset = 0.0


def measure(server_epoch: float, sent_at: float, received_at: float) -> float:
    global _offset
    rtt = received_at - sent_at
    if rtt < 0 or rtt > 5:
        return _offset
    _measurements.append(float(server_epoch) + 0.5 - (sent_at + received_at) / 2.0)
    median = statistics.median(_measurements)
    _offset = round(median, 2) if abs(median) >= 1.5 else 0.0
    return _offset


def offset() -> float:
    return _offset


def now() -> float:
    return time.time() + _offset


# ---- V16.8 graceful shutdown flag (set by SIGTERM/SIGINT so SSE generators end and uvicorn can finish) ----
import signal as _signal

_SHUTTING_DOWN = False


def shutting_down():
    return _SHUTTING_DOWN


def install_shutdown_signals():
    global _SHUTTING_DOWN
    for signum in (_signal.SIGTERM, _signal.SIGINT):
        try:
            previous = _signal.getsignal(signum)
        except (ValueError, OSError):
            continue

        def handler(received, frame, previous=previous):
            global _SHUTTING_DOWN
            _SHUTTING_DOWN = True
            if callable(previous):
                previous(received, frame)

        try:
            _signal.signal(signum, handler)
        except (ValueError, OSError):  # not the main thread
            pass

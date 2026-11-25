"""Shared worker process pool: heavy ML/DL fitting runs outside the event-loop process so Deriv ticks are never delayed."""
import asyncio
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

_pool = None


def pool():
    global _pool
    if _pool is None:
        workers = max(1, int(os.environ.get('TRAINING_WORKERS', str(max(1, (os.cpu_count() or 2) // 2)))))
        _pool = ProcessPoolExecutor(max_workers=workers, initializer=_limit_threads, mp_context=multiprocessing.get_context('spawn'))
    return _pool


def _limit_threads():
    # Leave CPU for the API/stream process: training workers use a bounded thread count.
    threads = os.environ.get('TRAINING_THREADS', str(max(1, (os.cpu_count() or 2) // 2)))
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
        os.environ[key] = threads
    try:
        import torch
        torch.set_num_threads(int(threads))
    except Exception:  # noqa: BLE001
        pass


async def run_in_worker(func, *args):
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(pool(), func, *args)
    except BrokenProcessPool:
        shutdown()
        return await loop.run_in_executor(pool(), func, *args)


def shutdown():
    global _pool
    if _pool is not None:
        # Kill running fits immediately: otherwise interpreter exit joins the workers and the restart hangs.
        for process in list(getattr(_pool, '_processes', {}).values()):
            try:
                process.terminate()
            except Exception:  # noqa: BLE001
                pass
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None

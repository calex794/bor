"""Liveness signals for actual polling and moderation sweeps.

Signals are written only after successful work, never merely on container start.
"""
import os
from pathlib import Path
import time
from aiogram import Bot
from aiogram.methods import GetUpdates


def beat(path: str | Path, name: str) -> None:
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f".bor-{name}"
    temporary = root / f".bor-{name}.{os.getpid()}.tmp"
    temporary.write_text(str(int(time.time())), encoding="ascii")
    os.replace(temporary, target)


class MonitoredBot(Bot):
    def __init__(self, *args, pulse_dir: str | Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.pulse_dir = pulse_dir

    async def __call__(self, method, request_timeout=None):
        answer = await super().__call__(method, request_timeout=request_timeout)
        if isinstance(method, GetUpdates):
            beat(self.pulse_dir, "polling")
        return answer

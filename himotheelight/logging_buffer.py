from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List


class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 500):
        super().__init__()
        self._items: Deque[Dict[str, str]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        item = {
            "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record),
        }
        with self._lock:
            self._items.append(item)

    def items(self, limit: int = 200) -> List[Dict[str, str]]:
        with self._lock:
            return list(self._items)[-max(1, limit):]

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from himotheelight import APP_NAME, __version__
from himotheelight.config_store import ConfigStore
from himotheelight.logging_buffer import MemoryLogHandler
from himotheelight.server import build_server


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def configure_logging(data_dir: Path):
    logger = logging.getLogger("himotheelight")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(levelname)s - %(message)s")

    memory = MemoryLogHandler(500)
    memory.setFormatter(formatter)
    logger.addHandler(memory)

    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(logs_dir / "himotheelight.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    if os.getenv("HIMOTHEELIGHT_CONSOLE_LOG") == "1" or not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console)
    return logger, memory


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} WLED controller")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()

    store = ConfigStore(Path(args.data_dir) if args.data_dir else None)
    logger, memory = configure_logging(store.data_dir)
    web_dir = resource_path("web")
    server = build_server(args.host, args.port, store, web_dir, memory)
    server.context.start()
    url = f"http://{args.host}:{args.port}"
    logger.info("%s v%s started at %s", APP_NAME, __version__, url)
    logger.info("Configuration: %s", store.path)

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        server.context.stop()
        server.server_close()
        logger.info("%s stopped", APP_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for the ambient renderer service."""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from .config import load_config
from .engine import RendererEngine
from .server import RendererHTTPServer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(args.config)
    engine = RendererEngine(config)
    server = RendererHTTPServer((args.bind, args.port), engine)
    shutdown_requested = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        if shutdown_requested.is_set():
            return
        shutdown_requested.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    engine.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

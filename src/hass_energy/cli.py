from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import signal
import sys
from pathlib import Path
from threading import Event

import uvicorn

from hass_energy.api.server import create_app
from hass_energy.config import load_app_config
from hass_energy.worker import Worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the hass-energy API and worker.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to YAML config",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(argv)

    log_level = _parse_log_level(args.log_level)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("hass_energy").setLevel(log_level)

    app_config = load_app_config(args.config)
    worker = Worker(app_config=app_config)
    shutdown_event = Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logging.info("Received signal %s, shutting down", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    app = create_app(app_config=app_config, worker=worker)
    worker.start()

    server = uvicorn.Server(
        config=uvicorn.Config(
            app,
            host=app_config.host,
            port=app_config.port,
            reload=False,
            log_level="info",
        )
    )

    server_task = server.serve()
    try:
        if inspect.iscoroutine(server_task):
            asyncio.run(server_task)
        else:
            _ = server_task
    finally:
        shutdown_event.set()
        if worker:
            worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _parse_log_level(level_str: str) -> int:
    normalized = level_str.strip().upper()
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    if normalized in mapping:
        return mapping[normalized]
    raise ValueError(f"Invalid log level: {level_str}")

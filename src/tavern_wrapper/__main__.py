"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn

from .config import ConfigError, load_config
from .monitor import Monitor
from .web import create_app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tavern-wrapper", description="Scheduled API health checks with Tavern"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path(os.environ.get("TAVERN_WRAPPER_CONFIG", "/config/services.yaml")),
        help="services file (env: TAVERN_WRAPPER_CONFIG)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("TAVERN_WRAPPER_HOST", "0.0.0.0"),  # noqa: S104
        help="listen address (env: TAVERN_WRAPPER_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TAVERN_WRAPPER_PORT", "8080")),
        help="listen port (env: TAVERN_WRAPPER_PORT)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run every check once, print the results and exit with 1 if any failed",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logging.getLogger("tavern_wrapper").error("%s", exc)
        return 2

    monitor = Monitor(config)
    if args.once:
        results = monitor.check_all()
        return 0 if all(r.ok for r in results) else 1

    uvicorn.run(create_app(monitor), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())

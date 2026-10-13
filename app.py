from __future__ import annotations

import argparse
import logging

from prometheus_client import REGISTRY, start_http_server

from .collector import RedfishCollector
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/redfish-exporter/config.yml")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config(args.config)
    REGISTRY.register(RedfishCollector(config))
    start_http_server(config.listen_port, addr=config.listen_address)
    logging.info("listening on %s:%s", config.listen_address, config.listen_port)
    import signal
    signal.pause()


if __name__ == "__main__":
    main()

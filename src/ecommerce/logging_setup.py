import json
import logging
import sys


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_event(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    logging.getLogger("ecommerce").info(json.dumps(payload, ensure_ascii=False))

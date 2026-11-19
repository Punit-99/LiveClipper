"""Central logging configuration.

Why a dedicated module? So every part of LiveClipper logs the same way, and so
we have ONE place that enforces the rule: never print credentials or tokens.

`get_logger(name)` returns a ready-to-use logger. `redact(value)` turns any
secret into a safe placeholder like "ab**********yz" for the rare case we must
mention that a token exists without revealing it.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def _configure_root() -> None:
    """Set up log formatting once for the whole process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given module name."""
    _configure_root()
    return logging.getLogger(name)


def redact(value: str | None) -> str:
    """Return a safe, non-reversible preview of a secret for logging.

    We NEVER log full tokens. If we must acknowledge one, we show only a hint:
        "abcd...wxyz"  ->  "ab**********yz"
    Short/empty values are fully masked.
    """
    if not value:
        return "<none>"
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"

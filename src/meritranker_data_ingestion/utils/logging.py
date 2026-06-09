"""Logging configuration for CLI and services."""

import logging
import sys


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the package root logger."""
    logger = logging.getLogger("meritranker_data_ingestion")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(levelname)s: %(message)s"),
        )
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger

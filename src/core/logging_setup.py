"""Centralized logging for the Content Engine.

Every module does `logger = logging.getLogger("content_engine.<sub>")` but nothing
ever attached a persistent sink — so logs only went to whatever stdout captured
(an overwritten temp file). This module fixes that: call `setup_logging()` once at
every entry point (scheduler daemon, planner, CLI tools) and ALL content_engine.*
child loggers write to a rotating on-disk file PLUS the console.

Usage:
    from src.core.logging_setup import setup_logging
    setup_logging()   # call first, before any logging happens
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_DEFAULT_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "logs",
)

_configured = False


def setup_logging(level: int = logging.INFO,
                  log_dir: str = _DEFAULT_LOG_DIR,
                  console: bool = True,
                  max_bytes: int = 5 * 1024 * 1024,
                  backup_count: int = 5) -> logging.Logger:
    """Configure the root 'content_engine' logger with a rotating file + console handler.

    Idempotent: safe to call multiple times; only configures once.
    Returns the root content_engine logger.
    """
    global _configured
    root = logging.getLogger("content_engine")
    if _configured:
        return root

    root.setLevel(level)
    # Don't propagate to the root logger (avoids duplicate/third-party noise).
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "content_engine.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Also keep a DEBUG-level full dump for deep diagnosis.
    debug_handler = RotatingFileHandler(
        os.path.join(log_dir, "content_engine.debug.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(fmt)
    root.addHandler(debug_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(level)
        stream.setFormatter(fmt)
        root.addHandler(stream)

    _configured = True
    root.info("Logging initialized -> %s", os.path.join(log_dir, "content_engine.log"))
    return root

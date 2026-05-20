"""Structured logging.

Off by default — call `configure_logging()` at startup to switch to JSON
output suitable for log aggregators (loki, datadog, cloudwatch).

Toggle via $LOG_FORMAT={json|text} (default "text") and
$LOG_LEVEL={DEBUG|INFO|WARNING|ERROR} (default "INFO").
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from typing import Optional


class JsonFormatter(logging.Formatter):
    """One JSON object per log line. Includes exc_info if present."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            etype, evalue, tb = record.exc_info
            out["exc_type"] = getattr(etype, "__name__", str(etype))
            out["exc_msg"] = str(evalue)
            out["traceback"] = "".join(traceback.format_exception(etype, evalue, tb))
        # Attach any structured extras: logger.info("...", extra={"k": v})
        for k, v in record.__dict__.items():
            if k in _STD_ATTRS:
                continue
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                out[k] = repr(v)
        return json.dumps(out, ensure_ascii=False, default=str)


_STD_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info", "thread",
    "threadName",
}


def json_formatter() -> logging.Formatter:
    return JsonFormatter()


def configure_logging(*, level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """Idempotent. Reads $LOG_LEVEL / $LOG_FORMAT when args are None.

    Replaces the root logger's existing handlers so calling twice doesn't
    duplicate output.
    """
    level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt = (fmt or os.getenv("LOG_FORMAT", "text")).lower()

    handler = logging.StreamHandler(stream=sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

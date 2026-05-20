"""Optional Sentry hook. Off unless $SENTRY_DSN is set.

`sentry-sdk` is not in requirements.txt; init_sentry() is a no-op without it.
Standard env config (release, environment, traces_sample_rate) is forwarded.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry() -> bool:
    """Returns True iff Sentry was initialised."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("$SENTRY_DSN set but sentry-sdk not installed; install with `pip install sentry-sdk`")
        return False
    sentry_sdk.init(
        dsn=dsn,
        release=os.getenv("SENTRY_RELEASE") or None,
        environment=os.getenv("SENTRY_ENVIRONMENT") or None,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        send_default_pii=False,
    )
    logger.info("sentry initialised (env=%s)", os.getenv("SENTRY_ENVIRONMENT") or "unset")
    return True

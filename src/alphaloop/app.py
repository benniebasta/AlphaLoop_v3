"""
Application factory — wires the DI container and all components.
"""

import logging

from alphaloop.core.config import AppConfig
from alphaloop.core.container import Container
from alphaloop.core.lifecycle import startup, shutdown
from alphaloop.monitoring.logging import setup_logging

logger = logging.getLogger(__name__)


async def create_app(
    config: AppConfig | None = None,
    *,
    symbol: str = "XAUUSD",
    instance_id: str = "",
    dry_run: bool = True,
) -> Container:
    """
    Create and initialize the full application.
    Returns the DI container with all components wired.
    """
    if config is None:
        config = AppConfig()

    setup_logging(config.log_level, json_output=False)

    container = Container(config)
    await startup(container)

    logger.info(
        "AlphaLoop v3 ready | symbol=%s | instance=%s | dry_run=%s",
        symbol, instance_id, dry_run,
    )

    return container

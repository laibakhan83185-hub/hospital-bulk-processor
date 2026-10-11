import logging

from app.config import settings


def setup_logging() -> None:
    """Configure application logging.

    The level comes from the LOG_LEVEL environment variable (default INFO).
    Set LOG_LEVEL=DEBUG to see per-hospital details.
    """
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # httpx logs every request at INFO; keep it quieter unless debugging.
    if settings.log_level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)

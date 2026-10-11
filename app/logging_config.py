import logging
import os


def setup_logging() -> None:
    """Configure application logging.

    The level comes from the LOG_LEVEL environment variable (default INFO).
    Set LOG_LEVEL=DEBUG to see per-hospital details.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # httpx logs every request at INFO; keep it quieter unless debugging.
    if level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)

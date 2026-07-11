import logging
from pathlib import Path

from .config import ROOT


def configure_logging() -> logging.Logger:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger("lol_helper")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_dir / "lol-helper.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger

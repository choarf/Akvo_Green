import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def get_logger(name: str):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_dir = Path(__file__).resolve().parent.parent / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / 'akvo_modbus.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')
    handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addHandler(console)
    return logger

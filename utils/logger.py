import logging
from logging.handlers import RotatingFileHandler
import os

LOG_FILE = os.path.join("logs", "app.log")
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("flipkart_app_bot")
logger.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

# file (rotating: 5 MB max, keep 3 backups)
_fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

# console
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
logger.addHandler(_ch)

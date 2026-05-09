import logging
import os
from datetime import datetime

import logfire

import config


def init():
    # Logfire handler
    logfire.configure()
    logfire_handler = logfire.LogfireLoggingHandler()

    # File handler
    os.makedirs(config.LOG_DIR, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    log_file = os.path.join(config.LOG_DIR, f"app_{timestamp}.log")

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)

    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(logfire_handler)

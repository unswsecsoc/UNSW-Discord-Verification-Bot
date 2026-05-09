import logging
import os
from datetime import datetime

import logfire
import psutil
from opentelemetry.metrics import CallbackOptions, Observation

import config


def init():
    # Logfire handler
    logfire.configure(
        service_name="bot",
        environment=config.ENVIRONMENT,
        send_to_logfire="if-token-present",
    )
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

    # More Logfire stuff
    logfire.instrument_system_metrics()
    logfire.metric_gauge_callback("system.disk.utilization", [disk_usage_callback])
    logfire.instrument_sqlite3()
    logfire.instrument_pydantic()


def disk_usage_callback(_options: CallbackOptions):
    usage = psutil.disk_usage("/")
    yield Observation(usage.percent / 100)

import logging

import logfire

def init():
    logfire.configure()

    # Send logging module logs to Logfire
    logfire.install_logging(
        level=logging.DEBUG,
        console=True,
    )

import os
import logging

"""
This handles logging for simclient

By default, this will log to ".simclient.log" in the current directory but can be changed by setting
    the SIMCLIENT_LOGFILE environment variable

This logger is also used by the default parameter interceptor function of the ClientMaker class
"""

logfile = os.getenv("SIMCLIENT_LOGFILE", ".simclient.log")
logging.basicConfig(
    filename=logfile,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    filemode="w",
)
logger = logging.getLogger("simclient")

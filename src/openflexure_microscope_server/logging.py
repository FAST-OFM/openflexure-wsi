"""Configure logging for the OpenFlexure Server.

A module containing a custom logging handler and helper function for
the OpenFlexure server. These handle formatting logs, ensuring that logs
are logged both to a file and to the terminal (see note below!). Functionality
is also provided for retrieving logs for to send over HTTP.

Note that when running in production OpenFlexure Microscope the server is run via
``systemd``. As such, the logs that are sent to the terminal (STDERR) or any other
output to STDOUT/STDERR are captured by ``systemd``. These can be viewed by using
``journalctl`` or the ``ofm journal`` command.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse

from labthings_fastapi.actions import ActionManager
from labthings_fastapi.logs import inject_invocation_id

LOGGER = logging.getLogger(__name__)
OFM_LOG_FILE: Optional[str] = None


def configure_logging(log_folder: str, debug: bool = False) -> None:
    """Configure logging for the server while it is running.

    Params:
    - log_folder: This modifies the root logger to have a rotating file handler and
    adds a custom handler that prints and stores all records except
    ``uvicorn.access`` logs.
    - debug: This modifies the root logger to change its logging level. It is
    set to True by starting the server with the cli argument ``--debug``.

    It is important not to let Uvicorn override these settings.

    """
    root_logger = logging.getLogger()

    if debug:
        root_logger.setLevel(logging.DEBUG)
    else:
        root_logger.setLevel(logging.INFO)

    # Explicitly make OFM_LOG_FILE a global so it can be updated based on log settings
    # This requires silencing PLW0603 which disallows globals.
    global OFM_LOG_FILE  # noqa: PLW0603
    OFM_LOG_FILE = os.path.join(log_folder, "openflexure_microscope.log")

    # Add OFM_HANDLER first so it can capture the error log if the
    # log file can't be accessed.
    ofm_format_str = "[%(asctime)s] [%(levelname)s] %(message)s"
    OFM_HANDLER.setFormatter(OFMLogFileFormatter(ofm_format_str))
    OFM_HANDLER.addFilter(UvicornAccessFilter())
    OFM_HANDLER.addFilter(inject_invocation_id)
    OFM_HANDLER.level = root_logger.level
    root_logger.addHandler(OFM_HANDLER)

    try:
        if not os.path.exists(log_folder):
            os.makedirs(log_folder)
        handler = RotatingFileHandler(
            filename=OFM_LOG_FILE,
            mode="a",
            maxBytes=1000000,
            backupCount=10,
        )
        format_str = (
            "[%(asctime)s] [%(levelname)s] <%(name)s>%(invocation_id_str)s %(message)s"
        )
        handler.setFormatter(OFMLogFileFormatter(format_str))
        handler.addFilter(UvicornAccessFilter())
        handler.addFilter(inject_invocation_id)
        root_logger.addHandler(handler)

    except PermissionError as e:
        LOGGER.error(f"Cannot create log file at {OFM_LOG_FILE}: {e}")

    LOGGER.info(
        "OFM server root logger has been set up at %s level",
        logging.getLevelName(root_logger.level),
    )


def retrieve_log() -> JSONResponse:
    """Return logs since the server started, up to a maximum of 250 messages.

    This log is the one shown in the UI and on the logging page.

    It does not include any of the ``uvicorn.access`` logs as these are emitted
    every time any API route is called.

    All logs, including ``uvicorn.access`` are logged to the OFM_LOG_FILE (see above)
    this is the best place to get logs about crashes.
    """
    return JSONResponse(content=jsonable_encoder(OFM_HANDLER.log_history))


def retrieve_log_from_file() -> PlainTextResponse:
    """Return the full log from the file for downloading.

    Note this is read and then sent as otherwise it causes a RuntimeError if it
    is written to while sending through FileResponse.
    """
    if OFM_LOG_FILE is None:
        raise HTTPException(
            500, "Cannot retrieve log file as logging directory hasn't been configured."
        )
    try:
        with open(OFM_LOG_FILE, "r", encoding="utf-8") as logfile:
            full_log = logfile.read()
        return PlainTextResponse(full_log)
    except IOError as e:
        raise HTTPException(
            500, "An error occurred while trying to access the log file."
        ) from e


class InvocationIdFormatter(logging.Formatter):
    """A formatter that annotates records with an invocation ID string.

    This only adds ``invocation_id_str`` for use in the format string; unlike
    ``OFMLogFileFormatter`` it does not rename ``uvicorn.error`` records.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Compute invocation_id_str from the record before formatting."""
        invocation_id = getattr(record, "invocation_id", None)
        record.invocation_id_str = (
            f" [invocation:{invocation_id}]" if invocation_id else ""
        )
        return super().format(record)


class OFMLogFileFormatter(logging.Formatter):
    """The formatter used for the OpenFlexure Microscope Server log file.

    This is the log that is then read by the GUI.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Adjust the logging formatting for uvicorn logs.

        uvicorn has two loggers. Each API access is ``uvicorn.access`` which we filter
        due to the noise. The other is ``uvicorn.error`` for more important messages.
        However, ``uvicorn.error`` is used for expected messages that are not errors,
        such as server start up. This can lead to people erroneously thinking that
        there is an error with their miroscope.
        """
        invocation_id = getattr(record, "invocation_id", None)
        record.invocation_id_str = (
            f" [invocation:{invocation_id}]" if invocation_id else ""
        )

        if record.name == "uvicorn.error" and record.levelno < logging.ERROR:
            record.name = "uvicorn"
        return super().format(record)


class OFMHandler(logging.Handler):
    """A logging.Handler that stores the most recent logs for access by the server and GUI."""

    def __init__(self, level: int = logging.INFO, max_logs: int = 250) -> None:
        """Initialise the handler with a set logging level and message buffer size.

        :param level: The level of logs captured. As standard logs of INFO and above
            are captured.
        :param max_logs: The maximum number of logs to hold in memory. This sets
            how many can be returned over HTTP.
        """
        super().__init__(level=level)
        self._log: list[dict] = []
        self._max_logs = max_logs
        # start at -1 so first record is 0
        self._running_counter = -1

    _action_manager: Optional[ActionManager] = None

    def set_action_manager(self, manager: ActionManager) -> None:
        """Set the action manager for finding the calling action of invocations."""
        self._action_manager = manager

    def _as_record_dict(self, record: logging.LogRecord) -> dict:
        """Format the record into the dictionary structure expected by the server."""
        msg = record.message
        self._running_counter += 1
        invocation_id = getattr(record, "invocation_id", None)
        calling_action = None
        if self._action_manager is not None and invocation_id is not None:
            try:
                invocation = self._action_manager.get_invocation(invocation_id)
                action = invocation.action_ref()
                if action is not None:
                    calling_action = action.name
            except KeyError:
                # If the action isn't found then continue with None.
                pass
        return {
            "timestamp": record.asctime,
            "level": record.levelname,
            "summary": msg.split("\n")[0],
            "message": msg,
            "invocation_id": invocation_id,
            "calling_action": calling_action,
            "logger": record.name,
            "sequence": self._running_counter,
        }

    def append_record(self, record: logging.LogRecord) -> None:
        """Append the record as a dictionary to a log of records.

        Any records in excess of the maximum number of logs are removed.
        """
        self._log.append(self._as_record_dict(record))
        while len(self._log) > self._max_logs:
            self._log.pop(0)

    def emit(self, record: logging.LogRecord) -> None:
        """Emit will save the logged record to the log."""
        try:
            # Always format before trying to read or the attributes are not set.
            self.format(record)
            if record.levelno >= self.level:
                self.append_record(record)
        except RecursionError as e:
            raise e
        except BaseException:
            # Never let the logger crash unless there is somehow recursion.
            # Use built in logging error handler
            self.handleError(record)

    @property
    def log_history(self) -> list[dict]:
        """Return the log history up to the maximum number of logs.

        Order is reversed so most recent logs are at the top.
        """
        return list(reversed(self._log))

    @property
    def get_formatted_log_history(self) -> str:
        """Return the log history as a formatted string.

        This is to be used for the fallback server, the formatting does not contain invocation
        IDs as no actions should have started.
        """
        output = ""
        for entry in self._log:
            output += f"[{entry['timestamp']} [{entry['level']}] <{entry['logger']}> {entry['message']}\n"
        return output


class UvicornAccessFilter(logging.Filter):
    """A logging filter to filter out "uvicorn.access" messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False if record is from "uvicorn.access"."""
        return not record.name.startswith("uvicorn.access")


OFM_HANDLER = OFMHandler()

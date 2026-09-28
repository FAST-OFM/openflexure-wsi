"""Test the server's logging configuration and handling."""

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from openflexure_microscope_server import logging as ofm_logging


@contextmanager
def tmp_logging_dir():
    """Yield a temporary logging dir, and delete rotating file handlers before closing.

    This is needed for unit tests on Windows for the temp dir to cleanup without an
    error.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
        root_logger = logging.getLogger()
        # Get the rotating file handlers. There should only be 1 (or none if mocked)
        file_handlers = [
            h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)
        ]
        for handler in file_handlers:
            handler.close()
            root_logger.removeHandler(handler)


def test_no_warnings_if_correct_permissions(caplog):
    """Check that configure_logging no warnings on normal operation.

    Checking that:

    * The logger can be set up without warnings.
    * That the log file is created.
    * That the log file contains the expected message about setting up the logger.
    """
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with caplog.at_level(logging.WARNING), tmp_logging_dir() as tmpdir:
        ofm_logging.configure_logging(tmpdir)
        assert len(caplog.records) == 0
        with open(ofm_logging.OFM_LOG_FILE, "r", encoding="utf-8") as log_file:
            log_txt = log_file.read()
        assert "OFM server root logger has been set up at INFO level" in log_txt
        root_logger = logging.getLogger()
        assert ofm_logging.OFM_HANDLER in root_logger.handlers


def test_permission_error_raises_warning(mocker, caplog):
    """Check that configure_logging raises warning on PermissionError."""
    mocker.patch(
        "openflexure_microscope_server.logging.RotatingFileHandler",
        side_effect=PermissionError("Permission denied"),
    )
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with caplog.at_level(logging.WARNING), tmp_logging_dir() as tmpdir:
        ofm_logging.configure_logging(tmpdir)
        assert len(caplog.records) == 1
        # Check OFM logger is added even if the file logger couldn't be.
        root_logger = logging.getLogger()
        assert ofm_logging.OFM_HANDLER in root_logger.handlers


def test_making_log_dir(caplog):
    """Check that configure_logging will make a dir if needed."""
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with caplog.at_level(logging.WARNING), tmp_logging_dir() as tmpdir:
        log_dir = os.path.join(tmpdir, "new_dir")
        assert not os.path.isdir(log_dir)
        ofm_logging.configure_logging(log_dir)
        assert len(caplog.records) == 0
        assert os.path.isdir(log_dir)


def test_max_logs():
    """Proclaim that only the most recent 250 logs are stored in memory."""
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with tmp_logging_dir() as tmpdir:
        ofm_logging.configure_logging(tmpdir)
        # But I would log five hundred times.
        for i in range(500):
            logging.info("log %s", i)
        logs = ofm_logging.OFM_HANDLER.log_history
        assert len(logs) == 250
        assert logs[-1]["message"] == "log 250"
        assert logs[0]["message"] == "log 499"
        # And I would log five hundred more.
        for i in range(500):
            logging.info("log %s", 500 + i)
        # Just to be the test who logged a thousand times to check your hand-el-or!
        logs = ofm_logging.OFM_HANDLER.log_history
        assert len(logs) == 250
        assert logs[-1]["message"] == "log 750"
        assert logs[0]["message"] == "log 999"


def test_ofm_handler_ignores_uvicorn_access():
    """Check uvicorn.access logs are ignored by custom handler.

    The uvicorn.access logger logs every time an endpoint is triggered.
    """
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with tmp_logging_dir() as tmpdir:
        ofm_logging.configure_logging(tmpdir)
        logs = ofm_logging.OFM_HANDLER.log_history
        starting_len = len(logs)
        logging.info("Root 1")
        uvicorn_logger = logging.getLogger("uvicorn.access")
        for i in range(500):
            uvicorn_logger.info("Uvicorn log %s", i)
        logging.info("Root 2")
        logs = ofm_logging.OFM_HANDLER.log_history
        assert len(logs) == starting_len + 2
        assert logs[starting_len]["message"] == "Root 1"
        assert logs[0]["message"] == "Root 2"


def test_server_responses():
    """Check that the FastAPI responses from the server are as expected.

    * retrieve_log() -> PlainTextResponse containing the last 250 logs separated by
        a new line
    * retrieve_log_from_file() -> All the logs from the log file.
    """
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with tmp_logging_dir() as tmpdir:
        ofm_logging.configure_logging(tmpdir)
        for i in range(500):
            logging.info("log %s", i)
        log_response = ofm_logging.retrieve_log()
        # Retrieve log should get a json that can be decoded into a list
        assert isinstance(log_response, JSONResponse)
        logs = json.loads(log_response.body.decode())
        assert isinstance(logs, list)
        assert len(logs) == 250
        file_log_response = ofm_logging.retrieve_log_from_file()
        assert isinstance(file_log_response, PlainTextResponse)
        file_logs = file_log_response.body.decode().split("\n")
        assert len(file_logs) > 500


def test_server_response_with_no_log_file():
    """Check that an HTTP exception is raised if logging isn't configured."""
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    # Also Reset log file global
    ofm_logging.OFM_LOG_FILE = None
    with pytest.raises(HTTPException) as excinfo:
        ofm_logging.retrieve_log_from_file()
    assert excinfo.value.status_code == 500


def test_server_response_with_no_log_dir():
    """Check that an HTTP exception is raised if the log file cannot be accessed."""
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with tmp_logging_dir() as tmpdir:
        ofm_logging.configure_logging(tmpdir)
        for i in range(500):
            logging.info("log %s", i)
        file_log_response = ofm_logging.retrieve_log_from_file()
        assert isinstance(file_log_response, PlainTextResponse)
    # Close and delete the log folder, this should create an error.
    with pytest.raises(HTTPException) as excinfo:
        ofm_logging.retrieve_log_from_file()
    assert excinfo.value.status_code == 500


FAKE_UVICORN_LOGGER = logging.getLogger("uvicorn.error")


@pytest.mark.parametrize(
    ("log_command", "names_in_log", "names_not_in_log"),
    [
        (FAKE_UVICORN_LOGGER.debug, [], ["<uvicorn>", "<uvicorn.error>"]),
        (FAKE_UVICORN_LOGGER.info, ["<uvicorn>"], ["<uvicorn.error>"]),
        (FAKE_UVICORN_LOGGER.warning, ["<uvicorn>"], ["<uvicorn.error>"]),
        (FAKE_UVICORN_LOGGER.error, ["<uvicorn.error>"], ["<uvicorn>"]),
        (FAKE_UVICORN_LOGGER.exception, ["<uvicorn.error>"], ["<uvicorn>"]),
    ],
)
def test_uvicorn_error_only_says_error_on_error(
    log_command, names_in_log, names_not_in_log
):
    """Check that an HTTP exception is raised if the log file cannot be accessed.

    Parametrised to check: debug doesn't log, info and warning log as <uvicorn>, and
    error logs as <uvicorn.error>.
    """
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()
    with tmp_logging_dir() as tmpdir:
        ofm_logging.configure_logging(tmpdir)
        log_command("Mockety mock mock!")
        with open(ofm_logging.OFM_LOG_FILE, "r", encoding="utf-8") as log_file:
            log_txt = log_file.read()
        for name in names_in_log:
            assert name in log_txt
        for name in names_not_in_log:
            assert name not in log_txt


@pytest.mark.parametrize(
    ("debug_flag", "expected_level"),
    [
        (False, logging.INFO),
        (True, logging.DEBUG),
    ],
)
def test_configure_logging_debug_mode(debug_flag, expected_level):
    """Check that debug flag sets the correct root and handler logging levels."""
    # Reset handler at start of test
    ofm_logging.OFM_HANDLER = ofm_logging.OFMHandler()

    with tmp_logging_dir() as tmpdir:
        # Run configuration with the parameterised debug flag
        ofm_logging.configure_logging(tmpdir, debug=debug_flag)

        root_logger = logging.getLogger()

        # Assert the root logger level was set correctly
        assert root_logger.level == expected_level
        # Assert the custom OFM handler level was synced to the root logger
        assert ofm_logging.OFM_HANDLER.level == expected_level

        # Cleanup: Remove the OFM_HANDLER from the root logger so it doesn't
        # interfere with other tests in the suite.
        root_logger.removeHandler(ofm_logging.OFM_HANDLER)


def test_log_file_formatter_includes_invocation_id():
    """Check that invocation IDs are included in logs when available."""
    formatter = ofm_logging.OFMLogFileFormatter(
        "[%(levelname)s]%(invocation_id_str)s %(message)s"
    )

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.invocation_id = "abc123"

    formatted = formatter.format(record)

    assert "[invocation:abc123]" in formatted
    assert "Test message" in formatted


def test_log_file_formatter_without_invocation_id():
    """Check that logs without invocation IDs still format correctly."""
    formatter = ofm_logging.OFMLogFileFormatter(
        "[%(levelname)s]%(invocation_id_str)s %(message)s"
    )

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)

    assert "[invocation:" not in formatted
    assert "Test message" in formatted

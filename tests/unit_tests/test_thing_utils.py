"""Test utility functions for things."""

import logging
import os
import re
import tempfile
import time

import pytest

import labthings_fastapi as lt

from openflexure_microscope_server.utilities import (
    coerce_thing_selector,
    get_invocation_logs,
    save_invocation_logs,
)

from ..shared_utils.lt_test_utils import LabThingsTestEnv


@pytest.mark.parametrize(
    ("selected", "default", "warning_count"),
    [
        ("a", "b", 1),
        (None, "b", 0),
        ("a", None, 1),
        (None, None, 0),
    ],
)
def test_coerce_with_empty_mapping(selected, default, warning_count, caplog):
    """Test None always returned for empty mappings, check warnings when expected.

    We expect warnings if the code tries to set a selected value that is not None,
    as None is the only valid answer.
    """
    with caplog.at_level(logging.WARNING):
        output = coerce_thing_selector(
            thing_mapping={}, selected=selected, default=default
        )
    assert output is None
    assert len(caplog.records) == warning_count


@pytest.mark.parametrize(
    ("selected", "default", "returned", "warning_count"),
    [
        ("b", "b", "b", 0),
        ("b", "a", "b", 0),
        ("b", None, "b", 0),
        ("b", "z", "b", 0),
        (None, "b", "b", 0),
        (None, "a", "a", 0),
        (None, None, "a", 0),
        (None, "z", "a", 1),
        ("z", "b", "b", 1),
        ("z", "a", "a", 1),
        ("z", None, "a", 1),
        ("z", "z", "a", 2),
    ],
)
def test_coerce_with_populated_mapping(
    selected, default, returned, warning_count, caplog
):
    """Test coercion of selected key for a populated thing mapping.

    The available things have names `a`, `b`, and `c`. These are the only valid
    returns.

    * If `selected` has a valid value it will be returned.
    * If `selected` is None `default` will be read, if `default` is a valid value
        it will be returned.
    * If `selected` or `default` have a non-`None` value that is not valid, then a
        warning is logged, but the value is treated as `None`.
    * Finally if no value is set, the first option in the mapping (`a`) is returned.

    Check that warnings are raised when appropriate.
    """
    thing_mapping = {"a": "Foo", "b": "Bar", "c": "FooBar"}
    with caplog.at_level(logging.WARNING):
        output = coerce_thing_selector(
            thing_mapping=thing_mapping, selected=selected, default=default
        )
    assert output == returned
    assert len(caplog.records) == warning_count


def simple_log_format(record: logging.LogRecord):
    """Format a log as just "[LEVEL] message"."""
    level = record.levelname
    msg = record.getMessage()
    return f"[{level}] {msg}\n"


class ThingThatLogs(lt.Thing):
    """A thing that has has actions that log."""

    @lt.action
    def log_and_capture(self, msg: str) -> str:
        """Log messages to the thing's logger, then capture and return them."""
        self.logger.info(msg)
        self.logger.warning(msg)
        self.logger.error(msg)
        logs = get_invocation_logs(self)
        logging_str = ""
        for record in logs:
            logging_str += simple_log_format(record)
        return logging_str

    @lt.action
    def log_and_capture_some(self, msg: str) -> str:
        """Log messages to the thing's logger, capture the last one and return it."""
        self.logger.info(msg)
        self.logger.warning(msg)
        before_err = time.time()
        self.logger.error(msg)
        logs = get_invocation_logs(self, logged_since=before_err)
        logging_str = ""
        for record in logs:
            logging_str += simple_log_format(record)
        return logging_str

    @lt.action
    def log_and_save(self, msg: str, filename: str, append: bool) -> float:
        """Log a message to the thing's logger."""
        self.logger.info(msg)
        self.logger.warning(msg)
        self.logger.error(msg)
        return save_invocation_logs(
            filename=filename,
            thing=self,
            append=append,
        )


@pytest.fixture
def logging_thing_test_env():
    """Yield a test environment with ThingThatLogs."""
    thing_conf = {"logging_thing": ThingThatLogs}
    with LabThingsTestEnv(things=thing_conf) as env:
        yield env


def test_get_invocation_logs(logging_thing_test_env):
    """Test that get_invocation_logs does return logs.

    This must be run in the invocation context to get the logs so the call
    to the function under test is actually in ``ThingThatLogs``.
    """
    client = logging_thing_test_env.get_thing_client("logging_thing")
    thing = logging_thing_test_env.get_thing_by_name("logging_thing")
    result = client.log_and_capture(msg="foobar")
    expected_message = "[INFO] foobar\n[WARNING] foobar\n[ERROR] foobar\n"
    assert result == expected_message
    # Can't be called directly as it must be in an action thread to get log context.
    with pytest.raises(lt.exceptions.NoInvocationContextError):
        thing.log_and_capture(msg="foobar")


def test_get_some_invocation_logs(logging_thing_test_env):
    """Test that get_invocation_logs does return logs with time filter."""
    client = logging_thing_test_env.get_thing_client("logging_thing")
    result = client.log_and_capture_some(msg="foobar")
    expected_message = "[ERROR] foobar\n"
    assert result == expected_message


def _assert_file_contents_matches_regex(filename: str, regex: str) -> None:
    """Assert that file contents matches a regex string."""
    with open(filename, "r") as file_obj:
        content = file_obj.read()

    assert re.match(regex, content) is not None


DATE_REGEX = r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]"

INFO_REGEX = DATE_REGEX + r" \[INFO\] foobar\n"
WARN_REGEX = DATE_REGEX + r" \[WARNING\] foobar\n"
ERR_REGEX = DATE_REGEX + r" \[ERROR\] foobar\n"


def test_save_invocation_logs(logging_thing_test_env):
    """Test that get_invocation_logs saves logs."""
    client = logging_thing_test_env.get_thing_client("logging_thing")
    with tempfile.TemporaryDirectory() as tmpdir:
        logpath = os.path.join(tmpdir, "log.txt")
        before_save = time.time()
        save_time = client.log_and_save(msg="foobar", filename=logpath, append=True)
        after_save = time.time()

        # The reported save time must fall within the action call.  Avoid a fixed
        # duration threshold: emulated architectures may take longer than 100 ms.
        assert before_save <= save_time <= after_save

        # Check there are 3 messages an inf a warning and an error
        _assert_file_contents_matches_regex(
            logpath,
            r"^" + INFO_REGEX + WARN_REGEX + ERR_REGEX + r"$",
        )

        # Run again with append=True and check that there are now 6 messages.
        client.log_and_save(msg="foobar", filename=logpath, append=True)
        _assert_file_contents_matches_regex(
            logpath,
            r"^"
            + INFO_REGEX
            + WARN_REGEX
            + ERR_REGEX
            + INFO_REGEX
            + WARN_REGEX
            + ERR_REGEX
            + r"$",
        )

        # Run again with append=False and check that only 3 message now remain.
        client.log_and_save(msg="foobar", filename=logpath, append=False)
        _assert_file_contents_matches_regex(
            logpath,
            r"^" + INFO_REGEX + WARN_REGEX + ERR_REGEX + r"$",
        )

"""Test server booting and shut down."""

import logging

import pytest
from fastapi import FastAPI

# Import as ofm server to attempt to minimise confusion with server as a var in other
# functions and also FastAPI `Server`.
from openflexure_microscope_server import server as ofm_server
from openflexure_microscope_server.things.camera import BaseCamera

from .test_server_config import SIM_CONFIG


def test_no_config():
    """Check that an error is thrown if no configuration is set for the microspe via CLI."""
    msg = "OpenFlexure Microscope Server must have a configuration file specified."
    with pytest.raises(RuntimeError, match=msg):
        ofm_server.serve_from_cli([])


def test_successful_start(mocker, caplog):
    """Check some basic things from the microscope setup.

    This test checks that logging is set up and that the actual function used for
    handling shutdown events shuts down the mjpeg streams.
    """
    mock_server = mocker.Mock()
    # Create a mock for the camera so we can check the MJPEG streams are closed on
    # shutdown.
    mock_camera = mocker.Mock(spec=BaseCamera)
    mock_server.things = {"camera": mock_camera}
    # Mock the LabThings function that returns the server so we have a mock server
    mocker.patch(
        "openflexure_microscope_server.server.lt.ThingServer.from_config",
        return_value=mock_server,
    )
    # Also mock configure_logging or it will touch a load of code and log.
    mock_log_configure = mocker.patch.object(ofm_server, "configure_logging")
    # Also mock customisation or it will try to access the hard drive and make files.
    mock_customise = mocker.patch.object(ofm_server, "customise_server")
    # And mock uvicorn.run as we don't want to start a server process
    mock_uvicorn_run = mocker.patch("openflexure_microscope_server.server.uvicorn.run")

    # Run the mock CLI
    ofm_server.serve_from_cli(["-c", SIM_CONFIG])

    # Check that the logging was configures, the server was customised, and uvicorn was
    # run
    assert mock_log_configure.call_count == 1
    assert mock_customise.call_count == 1
    assert mock_uvicorn_run.call_count == 1
    # Read the log config that was set
    log_config = mock_uvicorn_run.call_args.kwargs["log_config"]
    # Check both uvicorn loggers were set to propagate
    assert log_config["loggers"]["uvicorn"]["propagate"]
    assert log_config["loggers"]["uvicorn.access"]["propagate"]

    # Now Mock a shutdown signal being sent
    ofm_server.Server.handle_exit(mock_server.app, "mock_signal", "mock_frame")

    # Check
    assert mock_camera.kill_mjpeg_streams.call_count == 1

    # Mock shutdown again but with the MJPEG stream throwing a BaseException
    mock_camera.kill_mjpeg_streams.side_effect = BaseException("mock-54321")

    # This should log at error level but even a BaseException will not cause an error
    # that stops the shutdown signal propagating
    with caplog.at_level(logging.ERROR):
        ofm_server.Server.handle_exit(mock_server.app, "mock_signal", "mock_frame")
    # Assert there is one error log
    assert len(caplog.records) == 1
    # And that it is the message raised by the kill_mjpeg_streams mock
    assert str(caplog.records[0].msg) == "mock-54321"


def test_failed_customise(mocker):
    """Check what happens if there is an error before the server is started by uvicorn.

    This differs based on whether the --fallback flag is set.
    """
    mock_server = mocker.Mock()
    # Mock the LabThings function that returns the server so we have a mock server
    mocker.patch(
        "openflexure_microscope_server.server.lt.ThingServer.from_config",
        return_value=mock_server,
    )
    # Also mock customisation or it will try to access the hard drive and make files.
    mocker.patch.object(
        ofm_server, "customise_server", side_effect=RuntimeError("Can't touch this")
    )
    # And mock uvicorn.run as we don't want to start a server process
    mock_uvicorn_run = mocker.patch("openflexure_microscope_server.server.uvicorn.run")

    # Running the mock CLI will error
    with pytest.raises(RuntimeError, match="Can't touch this"):
        ofm_server.serve_from_cli(["-c", SIM_CONFIG])

    # But with the fallback flag uvicorn run will be run
    ofm_server.serve_from_cli(["-c", SIM_CONFIG, "--fallback"])

    assert mock_uvicorn_run.call_count == 1
    # Get the fallback app passed to uvicorn tun
    fallback_app = mock_uvicorn_run.call_args.args[0]
    # Check it really is a fastapi
    assert isinstance(fallback_app, FastAPI)
    # An that it has the error to display
    assert str(fallback_app._context.error) == "Can't touch this"


def test_debug_mode(mocker):
    """Test that --debug flag triggers lt.logs.configure_thing_logger."""
    # Mock the LabThings server creation so we can inspect its arguments
    mock_from_config = mocker.patch(
        "openflexure_microscope_server.server.lt.ThingServer.from_config",
        return_value=mocker.Mock(),
    )

    # Mock configure_logging to inspect its arguments and prevent side effects
    mock_configure_logging = mocker.patch.object(ofm_server, "configure_logging")

    # Mock other dependencies to avoid side effects (file writing, server starting)
    mocker.patch.object(ofm_server, "customise_server")
    mocker.patch("openflexure_microscope_server.server.uvicorn.run")

    # 1. Run with --debug
    ofm_server.serve_from_cli(["-c", SIM_CONFIG, "--debug"])

    # Verify configure_logging was called with debug=True (the second argument)
    assert mock_configure_logging.call_args.args[1] is True
    # Verify ThingServer.from_config was called with debug=True (the second argument)
    assert mock_from_config.call_args.args[1] is True

    # Reset the mocks for the next run
    mock_configure_logging.reset_mock()
    mock_from_config.reset_mock()

    # 2. Run without --debug
    ofm_server.serve_from_cli(["-c", SIM_CONFIG])

    # Verify configure_logging was called with debug=False
    assert mock_configure_logging.call_args.args[1] is False
    # Verify ThingServer.from_config was called with debug=False
    assert mock_from_config.call_args.args[1] is False

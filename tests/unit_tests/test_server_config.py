"""Test server booting and shut down."""

import os
from argparse import Namespace

import pytest

from labthings_fastapi.server.config_model import ThingServerConfig

# Import as ofm server to attempt to minimise confusion with server as a var in other
# functions and also FastAPI `Server`.
from openflexure_microscope_server import server as ofm_server
from openflexure_microscope_server.server import OFMApplicationData

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
FULL_CONFIG = os.path.join(REPO_ROOT, "ofm_config_full.json")
SIM_CONFIG = os.path.join(REPO_ROOT, "ofm_config_simulation.json")
MANUAL_CONFIG = os.path.join(REPO_ROOT, "ofm_config_manual.json")


@pytest.mark.parametrize("side_effect", [None, RuntimeError("Mock")])
def test_monkey_patched_handle_exit(side_effect, mocker):
    """Test that handle exit is monkey_patched."""
    mock_app = mocker.Mock()
    # Setup the same for any side_effect
    # Create mocks for FastAPI.Server, and for our custom shutdown function
    mock_shutdown_func = mocker.Mock(side_effect=side_effect)
    mock_fastapi_server = mocker.patch.object(ofm_server, "Server")
    original_mock_handle_exit = mock_fastapi_server.handle_exit
    handle_exit_args = (mock_app, "mock_signal", "mock_frame")

    ofm_server.set_shutdown_function(mock_shutdown_func)

    if side_effect is None:
        # In normal operation, both the custom shutdown function and the original
        # handle_exit should be called
        ofm_server.Server.handle_exit(*handle_exit_args)

        assert mock_shutdown_func.call_count == 1
        assert original_mock_handle_exit.call_count == 1
        # Check arguments are propagated correctly.
        assert original_mock_handle_exit.call_args.args == handle_exit_args
    else:
        # Use an error side effect to check that our custom shutdown function is called
        # before the standard `handle_exit`
        with pytest.raises(RuntimeError, match="Mock"):
            ofm_server.Server.handle_exit(*handle_exit_args)
        # The error was raised so we know our custom function was run, check that
        # handle_exit wasn't.
        # Note: that the non-mocked shutdown function is entirely wrapped in a
        # try/except BaseException, so that handle_exit will always run.
        assert original_mock_handle_exit.call_count == 0


def test_customise_server(mocker):
    """Check that all server customisation stages are called."""
    mock_server = mocker.Mock()
    # Mock everything to be called in customisation, partly setup may not be complete,
    # but also to limit excess coverage reporting.
    mocked_add_static = mocker.patch.object(ofm_server, "add_static_files")
    mocked_v2_endpoints = mocker.patch.object(ofm_server, "add_v2_endpoints")
    mocked_retrieve_log = mocker.patch.object(ofm_server, "retrieve_log")
    mocked_retrieve_log_file = mocker.patch.object(ofm_server, "retrieve_log_from_file")

    mock_server.api_prefix = "/api/v3/"
    mock_app = mock_server.app
    # The wrapper returned for app.get so we can see what functions are decorated.
    wrapper = mock_app.get.return_value

    application_data = OFMApplicationData(
        log_folder="mock_log_folder", data_folder="mock_data_folder"
    )
    # Finally we can run it!
    ofm_server.customise_server(mock_server, application_data)

    # Check each internal customisation function is called
    assert mocked_add_static.call_count == 1
    assert mocked_v2_endpoints.call_count == 1
    # Check app.get adds two routes
    assert mock_app.get.call_count == 2
    assert wrapper.call_count == 2

    # Check the routes and functions are as expected.
    added_routes = [call.args[0] for call in mock_app.get.call_args_list]
    assert "/api/v3/log/" in added_routes
    assert "/api/v3/logfile/" in added_routes
    wrapped_functions = [call.args[0] for call in wrapper.call_args_list]
    assert mocked_retrieve_log in wrapped_functions
    assert mocked_retrieve_log_file


def test_full_config_from_args_json():
    """Check that _full_config_from_args errors if json config is supplied."""
    msg = "OpenFlexure Microscope Server must have a configuration file specified."
    args = Namespace(
        config=None,
        json='{"things":{"example": "labthings_fastapi.example_things:MyThing"}}',
    )
    with pytest.raises(RuntimeError, match=msg):
        ofm_server._full_config_from_args(args)


@pytest.mark.parametrize(
    ("config", "log_dir", "data_dir"),
    [
        (FULL_CONFIG, "/var/openflexure/logs/", "/var/openflexure/data/"),
        (SIM_CONFIG, "./openflexure/logs/", "./openflexure/data/"),
        (MANUAL_CONFIG, "./openflexure/logs/", "./openflexure/data/"),
    ],
)
def test_full_config_from_args(config, log_dir, data_dir, mocker):
    """Check that _full_config_from_args returns as expected if json is supplied."""
    # Mock picamera library so LabThings can create its model.
    mocker.patch.dict(
        "sys.modules",
        {
            "picamera2": mocker.MagicMock(),
            "picamera2.encoders": mocker.Mock(),
            "picamera2.outputs": mocker.Mock(),
        },
    )
    args = Namespace(config=config, json=None)
    lt_conf = ofm_server._full_config_from_args(args)
    # If json is supplied OFM config is default. As the json is passed dircectly to
    # The Pydantic Mocel in LabThings. No custom data allowed.
    assert isinstance(lt_conf, ThingServerConfig)
    # Check the configuration validates
    application_config = OFMApplicationData(**lt_conf.application_config)
    # And has the correct values
    assert application_config.log_folder == log_dir
    assert application_config.data_folder == data_dir

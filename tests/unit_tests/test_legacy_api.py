"""Test server booting and shut down."""

import asyncio
from socket import gethostname

# Import as ofm server to attempt to minimise confusion with server as a var in other
# functions and also FastAPI `Server`.
from openflexure_microscope_server.server import legacy_api
from openflexure_microscope_server.things.camera import BaseCamera


def test_v2_endpoints(mocker):
    """Check that the expected v2 endpoints are added."""
    mock_server = mocker.Mock()
    # Mock the camera thing to mocke the lores_mjpeg stream get_frame()
    mock_server.things = {"camera": mocker.Mock(spec=BaseCamera)}
    mock_server.things["camera"].lores_mjpeg_stream.grab_frame = mocker.AsyncMock(
        return_value="Mock Frame"
    )
    mock_app = mock_server.app
    # The wrapper returned for app.get so we can see what functions are decorated.
    get_wrapper = mock_app.get.return_value
    head_wrapper = mock_app.head.return_value

    legacy_api.add_v2_endpoints(mock_server)

    assert get_wrapper.call_count == 3
    assert head_wrapper.call_count == 1

    # The calls for the get decorator and the internal wrapper
    get_and_wrapper_calls = zip(
        mock_app.get.call_args_list, get_wrapper.call_args_list, strict=True
    )
    # Pull out the first arg of each to get the route and wrapped function, save as a
    # dictionary - route: function
    routes = {
        get_call.args[0]: wrapper_call.args[0]
        for get_call, wrapper_call in get_and_wrapper_calls
    }

    # Check the fake routes are set
    assert "/routes" in routes
    fake_routes_dict = routes["/routes"]()
    assert isinstance(fake_routes_dict, dict)
    for fake_route in legacy_api.FAKE_ROUTES:
        assert fake_route in fake_routes_dict
        assert fake_routes_dict[fake_route]["url"] == fake_route
        assert fake_routes_dict[fake_route]["methods"] == ["GET"]

    # Check snapshot returns a jupeg response from the async get_frame of the
    # lores_mjpeg_stream
    assert "/api/v2/streams/snapshot" in routes
    # First get the async frame function from the head wrapper
    get_frames_func = head_wrapper.call_args.args[0]()
    assert routes["/api/v2/streams/snapshot"] == head_wrapper()

    jpg_response = asyncio.run(get_frames_func)
    assert isinstance(jpg_response, legacy_api.JPEGResponse)
    # Value should be set as mocked rather than a frame
    assert jpg_response.body.decode() == "Mock Frame"

    # Also check the name is the hostname.
    assert "/api/v2/instrument/settings/name" in routes
    assert routes["/api/v2/instrument/settings/name"]() == gethostname()

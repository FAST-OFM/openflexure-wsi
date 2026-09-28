"""Test the code that mounts static files to the server, without creating a server."""

import asyncio
import os
import shutil
import tempfile

import pytest
from starlette.responses import FileResponse, RedirectResponse

from openflexure_microscope_server.server import serve_static_files


@pytest.fixture
def mock_static_dir():
    """Return the path of a mock static directory.

    It contains enough files to be recognised as a webapp.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "assets"))
        with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as f_obj:
            f_obj.write("<mock>mock</mock>")
        yield tmpdir


@pytest.mark.parametrize(
    ("filename", "allow_cache"),
    [
        ("foo", False),
        ("woff2.foo", False),
        ("strange_file_ending_in_woff2", False),
        ("fontfile.woff2", True),
    ],
)
def test_add_static_file(filename, allow_cache, mocker):
    """Test running add_static_file with both font and non-font files.

    This should creates a wrapped function that returns a FileResponse, this
    should be have the path on disk and the no cache headers if not a woff2 font.
    """
    mock_app = mocker.Mock()
    # Get the wrapper function from the mocked decorator
    wrapper = mock_app.get.return_value
    serve_static_files.add_static_file(mock_app, fname=filename, folder="bar")

    # It is called once
    assert mock_app.get.call_count == 1
    # Check args for the get decorator
    assert len(mock_app.get.call_args.args) == 1
    assert mock_app.get.call_args.args[0] == "/" + filename
    assert len(mock_app.get.call_args.kwargs) == 2
    assert mock_app.get.call_args.kwargs["response_class"] == FileResponse
    assert not mock_app.get.call_args.kwargs["include_in_schema"]

    # Checked wrapper and the the returned wrapped function
    assert wrapper.call_count == 1
    wrapped = wrapper.call_args.args[0]
    # the wrapped function should return the file response
    response = wrapped()
    assert isinstance(response, FileResponse)
    assert response.path == os.path.join("bar", filename)
    # The file response headers always have some standard data, and if the file is
    # allowed to be cached it should also have the no_cache data
    for key, value in serve_static_files.NO_CACHE_HEADERS.items():
        if allow_cache:
            assert key not in response.headers
        else:
            # Check headers that refuse caching are present
            assert key in response.headers
            assert response.headers[key] == value


def test_add_static_with_no_static_dir(mocker):
    """Test that a FileNotFound error is raised if the static dir does not exist."""
    mock_app = mocker.Mock()
    # Mock STATIC_PATH to something that doesn't exist
    mocker.patch(
        "openflexure_microscope_server.server.serve_static_files.STATIC_PATH",
        "fakepath",
    )
    # Should raise as the mock static dir does not exist
    with pytest.raises(FileNotFoundError):
        serve_static_files.add_static_files(mock_app, data_folder=None)
    assert mock_app.get.call_count == 0


def test_check_static_dir(mock_static_dir, mocker):
    """Test check_static_dir recognises a basic webapp structure."""
    mocker.patch(
        "openflexure_microscope_server.server.serve_static_files.STATIC_PATH",
        mock_static_dir,
    )
    # First run shouldn't error as the mock dir has enough files.
    serve_static_files.check_static_dir()

    asset_path = os.path.join(mock_static_dir, "assets")
    index_path = os.path.join(mock_static_dir, "index.html")

    # Remove asset dir and check error.
    shutil.rmtree(asset_path)
    with pytest.raises(FileNotFoundError):
        serve_static_files.check_static_dir()
    # replace asset dir with file, it should still error.
    shutil.copyfile(index_path, asset_path)
    with pytest.raises(FileNotFoundError):
        serve_static_files.check_static_dir()

    # Return it to a folder and check everything works again
    os.remove(asset_path)
    os.makedirs(asset_path)
    serve_static_files.check_static_dir()

    # But it errors again if index.html is removed.
    os.remove(index_path)
    with pytest.raises(FileNotFoundError):
        serve_static_files.check_static_dir()


def test_add_static_files(mock_static_dir, mocker):
    """Check add_static_files mounts the internal webapp dirs, and sets endpoints for files."""
    mock_app = mocker.Mock()
    mocker.patch(
        "openflexure_microscope_server.server.serve_static_files.STATIC_PATH",
        mock_static_dir,
    )
    mock_server = mocker.Mock()
    mock_server.app = mock_app
    mock_server.api_prefix = "/api/v3"
    # Get the wrapper function from the mocked decorator
    wrapper = mock_app.get.return_value
    with tempfile.TemporaryDirectory() as datadir:
        serve_static_files.add_static_files(mock_server, data_folder=datadir)

    # Get should have been called twice to create a route for index
    assert mock_app.get.call_count == 2
    # Once at root as a redirict
    assert mock_app.get.call_args_list[0].args[0] == "/"
    assert mock_app.get.call_args_list[0].kwargs["response_class"] == RedirectResponse
    # Once at index.html as a file response
    assert mock_app.get.call_args_list[1].args[0] == "/index.html"
    assert mock_app.get.call_args_list[1].kwargs["response_class"] == FileResponse

    # The wrapper was also called twice to wrap two different functions
    assert wrapper.call_count == 2
    first_wrapped = wrapper.call_args_list[0].args[0]
    second_wrapped = wrapper.call_args_list[1].args[0]

    # The first wrapped function is the redirect to "index.html", it is async
    assert asyncio.run(first_wrapped()) == "/index.html"
    # The second is the file response for index.html, the path should be to the file on
    # disk
    assert second_wrapped().path == os.path.join(mock_static_dir, "index.html")
    # It shouldn't cache
    assert second_wrapped().headers["Pragma"] == "no-cache"

    # Also should have mounted both dirs
    assert mock_app.mount.call_count == 2
    mounted_path = mock_app.mount.call_args_list[0].args[0]
    mounted_dir = mock_app.mount.call_args_list[0].args[1].directory
    assert "/assets/" in mounted_path
    assert os.path.join(mock_static_dir, "assets") in mounted_dir

    assert mock_app.mount.call_args_list[1].args[0] == "/api/v3/data/"
    assert mock_app.mount.call_args_list[1].args[1].directory == datadir

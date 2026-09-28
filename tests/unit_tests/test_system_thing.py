"""Test the OpenFlexureSystem Thing *without* connecting it to a LabThings Server."""

import os
import sys
from signal import SIGTERM
from uuid import UUID

import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things import system
from openflexure_microscope_server.utilities import VersionData, robust_version_strings

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FAKE_OS_FILE = os.path.join(THIS_DIR, "assets", "os-release")
BROKEN_OS_FILE = os.path.join(THIS_DIR, "assets", "os-release-broken")
MISSING_OS_FILE = os.path.join(THIS_DIR, "assets", "this-does-not-exist")


@pytest.fixture
def system_thing():
    """Return a OpenFlexureSystem with a mocked server interface."""
    return create_thing_without_server(system.OpenFlexureSystem)


def _is_raspberrypi() -> bool:
    """Check if the machine running the test is a Raspberry Pi.

    This information is needed to check if the ``is_raspberrypi`` property of the Thing
    is correct. This is implemented in a different way to in the main code.
    """
    # Check if the file specifying the Raspberry Pi model exists
    if not os.path.isfile("/proc/device-tree/model"):
        return False
    with open("/proc/device-tree/model", "r", encoding="utf-8") as model_file:
        model_name = model_file.read()
    return "Raspberry Pi" in model_name


def test_is_raspberry(system_thing):
    """Check the thing property reports whether this is a Raspberry Pi correctly."""
    assert system_thing.is_raspberrypi == _is_raspberrypi()


@pytest.mark.parametrize(
    ("version_file", "expected_result"),
    [
        (MISSING_OS_FILE, "unknown"),
        (FAKE_OS_FILE, "trixie"),
        (BROKEN_OS_FILE, "unknown"),
    ],
)
def test_os_version_pi(version_file, expected_result, system_thing, mocker):
    """Check reading the operating system version from a Pi (mocked!)."""
    mock_is_pi = mocker.PropertyMock(return_value=True)
    mocker.patch.object(type(system_thing), "is_raspberrypi", mock_is_pi)

    mocker.patch(
        "openflexure_microscope_server.things.system.OS_VERSION_FILE", version_file
    )
    assert system_thing.os_version == expected_result


def test_os_version_not_pi(system_thing, mocker):
    """Check reading the operating system when not on a Pi returns None."""
    mocker.patch(
        "openflexure_microscope_server.things.system.OS_VERSION_FILE", FAKE_OS_FILE
    )
    mock_is_pi = mocker.PropertyMock(return_value=False)
    mocker.patch.object(type(system_thing), "is_raspberrypi", mock_is_pi)
    assert system_thing.os_version is None


def test_version_data(system_thing):
    """Check VersionData is returned.

    The content of robust_version_strings() is tested elsewhere.
    """
    data = system_thing.version_data
    assert isinstance(data, VersionData)
    assert data == robust_version_strings()

    fake_data = VersionData(version="fake", version_source="fake")
    system_thing._version_data = fake_data
    data = system_thing.version_data
    assert data == fake_data


def test_hostname(system_thing, mocker):
    """Check the hostname matches what socket.gethostname() returns."""
    mock_gethostname = mocker.patch("socket.gethostname", return_value="foobar")
    assert system_thing.hostname == "foobar"
    mock_gethostname.assert_called_once_with()


def test_microscope_id(system_thing, mocker):
    """Check the microscope UUID is a valid UUID and doesn't change when read again."""
    expected_id = UUID("01234567-89ab-cdef-0123-456789abcdef")
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data=expected_id.hex))
    microscope_id = system_thing.microscope_id
    assert isinstance(microscope_id, UUID)
    assert microscope_id == expected_id
    assert microscope_id == system_thing.microscope_id


def test_thing_state(system_thing, mocker):
    """Check the thing state contains version data, hostname, and a UUID string."""
    mocker.patch("socket.gethostname", return_value="foobar")
    microscope_id = UUID("01234567-89ab-cdef-0123-456789abcdef")
    mock_id = mocker.PropertyMock(return_value=microscope_id)
    mocker.patch.object(type(system_thing), "microscope_id", mock_id)
    version_data = robust_version_strings()
    state_dict = system_thing.thing_state
    assert state_dict["hostname"] == "foobar"
    # Check the UUID in the dictionary is a string
    assert isinstance(state_dict["microscope-uuid"], str)
    # Check uuid string can be converted to valid UUID
    UUID(state_dict["microscope-uuid"])
    assert state_dict["version"] == version_data.version
    assert state_dict["version_source"] == version_data.version_source


def test_check_shutdown_commands():
    """Check the shutdown and reboot commands are as expected."""
    # Check the shutdown commands are as expected.
    assert system.LEGACY_SHUTDOWN_CMD == ["sudo", "shutdown", "-h", "now"]
    assert system.OFM_SHUTDOWN_CMD == ["ofm", "system-shutdown"]
    # Check the reboot command are as expected.
    assert system.LEGACY_REBOOT_CMD == ["sudo", "shutdown", "-r", "now"]
    assert system.OFM_REBOOT_CMD == ["ofm", "system-restart"]


def test_pi_shutdown_and_restart(system_thing, mocker):
    """Check that on a Pi will receive the correct shutdown command."""
    # Mock the commands as we don't want to shutdown when running tests.
    mocker.patch.object(
        system,
        "LEGACY_SHUTDOWN_CMD",
        new=[sys.executable, "-c", "print('legacy shutdown')"],
    )
    mocker.patch.object(
        system,
        "OFM_SHUTDOWN_CMD",
        new=[sys.executable, "-c", "print('ofm shutdown')"],
    )
    mocker.patch.object(
        system,
        "LEGACY_REBOOT_CMD",
        new=[sys.executable, "-c", "print('legacy reboot')"],
    )
    mocker.patch.object(
        system,
        "OFM_REBOOT_CMD",
        new=[sys.executable, "-c", "print('ofm reboot')"],
    )

    # Pretend to be a pi.
    mock_is_pi = mocker.PropertyMock(return_value=True)
    mocker.patch.object(type(system_thing), "is_raspberrypi", mock_is_pi)

    # Check on trixie the ofm commands are used.
    mock_os_version = mocker.PropertyMock(return_value="trixie")
    mocker.patch.object(type(system_thing), "os_version", mock_os_version)
    result = system_thing.shutdown()
    assert result.output.strip() == "ofm shutdown"
    assert result.error.strip() == ""
    result = system_thing.reboot()
    assert result.output.strip() == "ofm reboot"
    assert result.error.strip() == ""

    # Otherwise the legacy commands are used.
    mock_os_version = mocker.PropertyMock(return_value="bookworm")
    mocker.patch.object(type(system_thing), "os_version", mock_os_version)
    result = system_thing.shutdown()
    assert result.output.strip() == "legacy shutdown"
    assert result.error.strip() == ""
    result = system_thing.reboot()
    assert result.output.strip() == "legacy reboot"
    assert result.error.strip() == ""


def test_non_pi_shutdown(system_thing, mocker):
    """Check that a server not on a pi runs os.kill when asked to shutdown.

    It should do this instead of running the shutdown command in a subprocess.
    """
    # Mock os.kill
    mock_kill = mocker.patch("os.kill")
    # Get this subprocess
    pid = os.getpid()

    mock_is_pi = mocker.PropertyMock(return_value=False)
    mocker.patch.object(type(system_thing), "is_raspberrypi", mock_is_pi)

    result = system_thing.shutdown()

    # Check it tried to kill this process with SIGTERM
    mock_kill.assert_called_once_with(pid, SIGTERM)
    # Check output is an appropriate error message (that we have not shut down!)
    assert result.output.strip() == ""
    assert "but the server has not shutdown" in result.error


def test_non_pi_reboot(system_thing, mocker):
    """Check that a server not on a pi refuses to restart."""
    mock_is_pi = mocker.PropertyMock(return_value=False)
    mocker.patch.object(type(system_thing), "is_raspberrypi", mock_is_pi)
    result = system_thing.reboot()

    # Check output is an appropriate error message
    assert result.output.strip() == ""
    assert result.error.strip() == "Restart is only available on Raspberry Pi."

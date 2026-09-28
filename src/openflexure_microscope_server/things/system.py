"""OpenFlexure System.

A module to control the underlying microscope system and to expose information about
the microscope, server, and thing states to the web API.
"""

import os
import re
import socket
import subprocess
import time
from collections.abc import Mapping
from signal import SIGTERM
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel

import labthings_fastapi as lt

from openflexure_microscope_server.utilities import VersionData, robust_version_strings

LEGACY_SHUTDOWN_CMD = ["sudo", "shutdown", "-h", "now"]
OFM_SHUTDOWN_CMD = ["ofm", "system-shutdown"]
LEGACY_REBOOT_CMD = ["sudo", "shutdown", "-r", "now"]
OFM_REBOOT_CMD = ["ofm", "system-restart"]

OS_VERSION_FILE = "/usr/lib/os-release"


class CommandOutput(BaseModel):
    """A pydantic model passing the STDOUT and STDERR from a subprocess over HTTP."""

    output: str
    error: str


class OpenFlexureSystem(lt.Thing):
    """Describe and control the OpenFlexure system.

    This Thing:

    * Exposes information about the Microscope, Server, and Thing states to the web
        API.
    * Controls the underlying OS on the Raspberry Pi allowing shutdown and restarting
        of the system.
    """

    _class_settings = {"validate_properties_on_set": True}

    _microscope_id: Optional[UUID] = None

    @lt.property
    def microscope_id(self) -> Optional[UUID]:
        """A unique identifier for this microscope.

        This is a number set on first boot of the OS. For simulation or manual
        microscopes running on Windows this will be None.
        """
        if self._microscope_id is None and os.path.exists("/etc/machine-id"):
            with open("/etc/machine-id", "r") as file_obj:
                ident = file_obj.read().strip()
            self._microscope_id = UUID(ident)
        return self._microscope_id

    _hardware_id: Optional[str] = None

    @lt.property
    def hardware_id(self) -> Optional[str]:
        """A unique identifier for the Microscope CPU.

        This may be unset on simulation or manual microscopes.
        """
        sn_path = "/sys/firmware/devicetree/base/serial-number"
        if self._hardware_id is None and os.path.exists(sn_path):
            with open(sn_path, "r") as file_obj:
                self._hardware_id = file_obj.read().rstrip("\x00")
        return self._hardware_id

    @lt.property
    def hostname(self) -> str:
        """The hostname of the microscope, as reported by its operating system."""
        return socket.gethostname()

    _version_data: Optional[VersionData] = None

    @lt.property
    def version_data(self) -> VersionData:
        """The version string and version source for the server.

        The source may be a commit hash if installed from git. "TOML" if installed from
        source, or "Dist" if installed from a distribution package.

        If the version or its source cannot be determined an error will be Logged.
        """
        # Don't save as a setting as it may change. Get the version string the first
        # time the property is requested this boot.
        if self._version_data is None:
            self._version_data = robust_version_strings()
        return self._version_data

    @lt.property
    def is_raspberrypi(self) -> bool:
        """Return True if running on a Raspberry Pi."""
        return os.path.exists("/usr/bin/raspi-config")

    @lt.property
    def os_version(self) -> Optional[str]:
        """Return the OS version of the Pi. Returns None if not on a Pi."""
        if not self.is_raspberrypi:
            return None

        if not os.path.isfile(OS_VERSION_FILE):
            return "unknown"

        with open(OS_VERSION_FILE, "r", encoding="utf-8") as os_file:
            os_data = os_file.read()

        version_match = re.search(r"^VERSION_CODENAME=(.+)$", os_data, re.MULTILINE)
        if not version_match:
            return "unknown"

        return version_match.group(1)

    @lt.action
    def shutdown(self) -> CommandOutput:
        """Attempt to shutdown the device."""
        if not self.is_raspberrypi:
            os.kill(os.getpid(), SIGTERM)
            time.sleep(0.5)
            # If this line is reached then the server did not shut down!
            return CommandOutput(
                output="",
                error="Shutdown command sent to server process, but the server has not shutdown.",
            )

        cmd = OFM_SHUTDOWN_CMD if self.os_version == "trixie" else LEGACY_SHUTDOWN_CMD

        p = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

        out, err = p.communicate()
        return CommandOutput(output=out, error=err)

    @lt.action
    def reboot(self) -> CommandOutput:
        """Attempt to reboot the device."""
        if not self.is_raspberrypi:
            return CommandOutput(
                output="",
                error="Restart is only available on Raspberry Pi.",
            )

        cmd = OFM_REBOOT_CMD if self.os_version == "trixie" else LEGACY_REBOOT_CMD

        p = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        out, err = p.communicate()
        return CommandOutput(output=out, error=err)

    @lt.action
    def get_things_state(self) -> Mapping:
        """Metadata summarising the current state of all Things in the server."""
        return self._thing_server_interface.get_thing_states()

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Summary metadata describing the current state of the Thing."""
        state = {
            "hostname": self.hostname,
            "microscope-uuid": str(self.microscope_id),
            "version": self.version_data.version,
            "version_source": self.version_data.version_source,
        }
        if self.hardware_id is not None:
            state["hardware_id"] = self.hardware_id
        return state

#! /usr/bin/env python3
"""Start a server subprocess for testing the server's life cycle.

This test spools up a server in a sub process. Connects to an MJPEG stream, runs a
couple of other tests. Most importantly it checks the server shuts down gracefully
despite the MJPEG stream being connected.

These tests are separated from unit tests to avoid inflating test coverage.
This file should be run directly rather than through a test framework.

They are designed to run on CI and should work on Linux or WSL for local debugging.
"""

import os
import shutil
import subprocess
from time import sleep, time
from typing import Optional

from PIL import Image

import labthings_fastapi as lt

THIS_DIR: str = os.path.dirname(os.path.realpath(__file__))
WORKING_DIR: str = os.path.join(THIS_DIR, "working_dir")
REPO_DIR: str = os.path.dirname(os.path.dirname(THIS_DIR))
CONFIG_FILE: str = os.path.join(REPO_DIR, "ofm_config_simulation.json")
SERVER_CMD: list[str] = [
    "openflexure-microscope-server",
    "-c",
    CONFIG_FILE,
]


def main() -> None:
    """Set up the server, run basic checks, shutdown, check for graceful exit.

    The basic checks include checks that:
      - The server boots
      - The server reports the expected IP and port
      - A ThingClient can connect to the camera
      - The client grab a frame from the stream, and it is the expected size
      - A background process can subscribe to the stream
    """
    set_up_working_dir()
    os.chdir(WORKING_DIR)

    server_process: Optional[subprocess.Popen] = None
    subscriber_process: Optional[subprocess.Popen] = None
    try:
        print("Starting server")
        server_process = start_server()
        error_if_server_not_started(server_process, timeout=15)

        test_client_connection()

        # This also sleeps for 2s and checks it is still connected
        subscriber_process = subscribe_to_mjpeg_stream()

        server_process.terminate()
        sleep(3)

        check_for_graceful_shutdown(server_process)

    finally:
        # Ensure the server and subscriber processes are really dead
        if subscriber_process is not None and subscriber_process.poll() is None:
            subscriber_process.kill()
        if server_process is not None and server_process.poll() is None:
            server_process.kill()
            raise RuntimeError("Server needed killing at end of test.")


def test_client_connection() -> None:
    """Check a ThingClient can interact with the simulation microscope camera."""
    print("Connecting Python client to microscope, and capturing image")
    cam_client = lt.ThingClient.from_url("http://localhost:5000/api/v3/camera/")
    img = Image.open(cam_client.grab_jpeg().open())
    print(f"Successfully grabbed image of size {img.size}")
    assert img.size == (820, 616)
    print("Successfully grabbed image from camera mjpeg stream")


def subscribe_to_mjpeg_stream() -> subprocess.Popen:
    """Start a background process subscribed to the mjpeg stream.

    :returns: The Popen object for the ongoing process.

    :raises: RuntimeError if the stream is not still connected after 2s
    """
    # Use nohup to stop process hanging up unexpectedly. Explicitly forward stream
    # to /dev/null to keep curl connected.

    curl_command = [
        "nohup",
        "curl",
        "-s",
        "http://localhost:5000/api/v3/camera/mjpeg_stream",
        ">",
        "/dev/null",
        "2>&1",
    ]

    process = subprocess.Popen(
        curl_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )
    os.set_blocking(process.stdout.fileno(), False)

    sleep(2)
    if process.poll() is not None:
        raise RuntimeError(
            "MJPEG Subscriber process is not running. This likely means it could not"
            " stay connected to the stream.\n"
        )
    return process


def set_up_working_dir() -> None:
    """If working dir exists, delete it and make a new one."""
    if os.path.exists(WORKING_DIR):
        shutil.rmtree(WORKING_DIR)
    os.makedirs(WORKING_DIR)


def start_server() -> subprocess.Popen:
    """Start the server in a subprocess.

    The server is started in a subprocess and all outputs are buffered.

    :returns: Popen object for the ongoing process
    """
    process = subprocess.Popen(
        SERVER_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )
    os.set_blocking(process.stdout.fileno(), False)
    return process


def error_if_server_not_started(
    server_process: subprocess.Popen, timeout: float = 15.0
) -> None:
    """Check the server started up as expected.

    Check the subprocess is running
    Check the logs have no errors
    Read the Logs to check uvicorn is running on http://127.0.0.1:5000

    :param server_process: The Popen object for the the server process.

    :raises RuntimeError: If the server is not running as expected.
    """
    confirmed_uvicorn_is_running = False

    t_start = time()
    while not confirmed_uvicorn_is_running and time() - t_start < timeout:
        sleep(0.2)
        if stdout := read_process_buffers(server_process):
            print("".join(stdout), end="")
            if server_process.poll() is not None:
                raise RuntimeError("Server process is not running!")

            for line in stdout:
                if line.startswith("ERROR:"):
                    raise RuntimeError(f"Server started up with error\n{line}")
                if "Uvicorn running on http://127.0.0.1:5000" in line:
                    confirmed_uvicorn_is_running = True

    if not confirmed_uvicorn_is_running:
        raise RuntimeError("Cannot confirm Uvicorn is running on http://127.0.0.1:5000")

    # If we reached here Everything is fine!
    print("Server is running as expected\n\n")


def check_for_graceful_shutdown(server_process: subprocess.Popen) -> None:
    """Check the server shutdown gracefully.

    Check the subprocess is not running
    Check the logs have no errors
    Read the Logs to check the shutdown was graceful, rather than killed on Uvicorn
    timeout

    :param server_process: The Popen object for the the server process.

    :raises RuntimeError: If the server didn't shutdown gracefully.
    """
    stdout = read_process_buffers(server_process)
    print("".join(stdout))
    print("\n\n")
    for line in stdout:
        if line.startswith("ERROR:"):
            raise RuntimeError(f"Server encountered error\n{line}")
        if "graceful shutdown exceeded" in line:
            # This should be logged as an ERROR. This check is belts and braces.
            raise RuntimeError("Server failed to shutdown gracefully.")


def read_process_buffers(process: subprocess.Popen) -> list[str]:
    """Return STDOUT from a process."""
    stdout = []

    while line := process.stdout.readline():
        stdout.append(line)

    return stdout


if __name__ == "__main__":
    main()

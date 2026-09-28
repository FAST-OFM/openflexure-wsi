#! /usr/bin/env python3
"""Generate data about the HTTP API."""

import json
import os
from unittest import mock

from fastapi.testclient import TestClient

import labthings_fastapi as lt

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def mock_picamera2_import() -> None:
    """Mock the picamera2 library so that the full config can be loaded."""
    dummy_cam = mock.Mock()
    mock_picamera2 = mock.MagicMock()
    mock_picamera2.return_value.__enter__.return_value = dummy_cam
    mock_picamera2.return_value.__exit__.return_value = None
    mock.patch.dict(
        "sys.modules",
        {
            "picamera2": mock_picamera2,
            "picamera2.encoders": mock.Mock(),
            "picamera2.outputs": mock.Mock(),
        },
    ).start()


def main() -> None:
    """Generate openapi description and Thing descriptions."""
    mock_picamera2_import()
    config = os.path.join(ROOT_DIR, "ofm_config_full.json")

    with open(config, "r", encoding="utf-8") as file_obj:
        config_dict = json.load(file_obj)

    config_dict["application_config"]["data_folder"] = "/tmp/data"

    server = lt.ThingServer(things=config_dict["things"])
    test_client = TestClient(server.app)
    td_response = test_client.get("/thing_descriptions/")
    with open("apidocs/http/thing_descriptions.json", mode="w") as file_obj:
        json.dump(td_response.json(), file_obj, indent=4)
    openapi_response = test_client.get("/openapi.json")
    with open("apidocs/http/openapi.json", mode="w") as f:
        json.dump(openapi_response.json(), f, indent=4)


if __name__ == "__main__":
    main()

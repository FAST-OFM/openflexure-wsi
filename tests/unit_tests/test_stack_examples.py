"""Unit tests for validating stack classification logic in AutofocusThing.

This module tests whether the `check_stack_result` method correctly classifies
z-stack sharpness profiles into "success", "continue", or "restart" categories
based on predefined test cases.

Sharpness profiles are loaded from a JSON file and converted into mock capture
objects to simulate real camera captures.
"""

import json
import os

import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.focus.autofocus import AutofocusThing

THIS_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(THIS_DIR, "data", "sharpness_test_cases.json")


class MockCapture:
    """Simple mock object representing a captured image.

    This class mimics the minimal interface required by
    `check_stack_result` by adding the `sharpness` and `buffer_id`
    attributes.

    :param sharpness: The sharpness value associated with the image.
    :param buffer_id: A unique identifier for the image buffer.
    """

    def __init__(self, sharpness, buffer_id):
        """Give each capture a sharpness and buffer_id."""
        self.sharpness = sharpness
        self.buffer_id = buffer_id


def make_captures(sharpness_list):
    """Convert a list of sharpness values into mock capture objects.

    :param sharpness_list: A list of numeric sharpness values.
    :returns: A list of MockCapture instances with sequential buffer IDs.
    """
    return [MockCapture(s, i) for i, s in enumerate(sharpness_list)]


def load_cases():
    """Load the sharpness data from the json.

    Includes the sharpnesses to test, manually written labels
    on the required result, and whether the test is allowed to
    fail. This ensures future tests will flag any regression on
    tests while allowing improvements.
    """
    with open(DATA_PATH) as f:
        data = json.load(f)

    params = []
    for i, case in enumerate(data):
        expected = case["label"]

        # Allow multiple acceptable labels
        if not isinstance(expected, list):
            expected = [expected]

        marks = []

        if case.get("allow_failure", False):
            marks.append(pytest.mark.xfail(reason="Known failing case"))

        params.append(
            pytest.param(
                case["sharpnesses"],
                expected,
                marks=marks,
                id=f"case_{i}",
            )
        )
    return params


@pytest.mark.parametrize(("sharpnesses", "expected"), load_cases())
def test_stack_labelling(sharpnesses, expected):
    """Test stack classification accuracy against labelled sharpness cases.

    This test loads the test from load_cases and evaluates
    the classification returned by `check_stack_result`.

    The test ensures that only cases marked with "allow_failure: true" can fail.

    Expected labels may be a single value or a list of acceptable values.
    """
    autofocus_thing = create_thing_without_server(AutofocusThing, mock_all_slots=True)

    captures = make_captures(sharpnesses)

    result, _ = autofocus_thing.check_stack_result(captures, check_turning_points=False)

    assert result in expected

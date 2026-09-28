"""Testing the server specified user interface."""

import os

import pytest

from openflexure_microscope_server import ui

from ..shared_utils.md_test_cases import find_test_cases

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_SANITISATION_TEST_FILE = os.path.join(
    THIS_DIR, "data", "html_sanitisation_test_cases.md"
)
HTML_SANITISATION_TEST_CASES = find_test_cases(HTML_SANITISATION_TEST_FILE, "html")


@pytest.mark.parametrize("test_case", HTML_SANITISATION_TEST_CASES)
def test_html_block_sanitises(test_case):
    """Check each case in the test case file sanitises as expected."""
    sanitised = ui.sanitise_html(test_case.input_code).strip()
    assert sanitised == test_case.result_code, (
        f"Test case: '{test_case.title}': Input code doesn't sanitise as expected."
    )


def test_help_block_is_plain_compact_copy():
    """Help text is plain text so the client can render it without v-html."""
    block = ui.HelpBlock(label="Current geometry", text="A\x00 10 × 20 µm field")
    assert block.element_type == "help_block"
    assert block.text == "A 10 × 20 µm field"

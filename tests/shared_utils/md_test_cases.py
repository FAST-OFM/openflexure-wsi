"""Read multi-line test cases from markdown files.

When checking if text coercion is working multiline Python can be very difficult
to read and maintain. This file allows the input and expected results to be written
into markdown codeblocks.

Each test is a second level heading.
"""

import re

# Level 2 markdown heading. Group 1 is the text
CASE_REGEX = re.compile(r"^##\s(.*)", re.MULTILINE)
# Level 3 markdown heading. Group 1 is the text
LEVEL3_REGEX = re.compile(r"^###\s(.*)", re.MULTILINE)
# Markdown code block.
# Group 1 is the language specification after the back ticks
# Group 2 is the code
CODE_REGEX = re.compile(
    r"^```([a-zA-Z]+)?\s?\n(?:(.*?)\n)?```", re.MULTILINE | re.DOTALL
)


class MdTestCase:
    """A class to represent a test case read from a markdown file.

    Useful attributes:

    * title - The title of the test case
    * input_code - the input code for the test case
    * result_code - the expected result code for the test case
    """

    def __init__(self, title: str, case_text: str, block_type: str) -> None:
        """Initialise the test case.

        :param title: The title of the test case.
        :param case_text: The full text of the test case.
        :param block_type: The type of code block expected (the label after the triple
            backticks).
        """
        self.title = title
        self.block_type = block_type
        self.input_code, self.result_code = self._parse(case_text)

    def __repr__(self):
        """Return the string representation of the test case."""
        return f"<MdTestCase: {self.title}>"

    def _parse(self, case_text: str) -> tuple[str, str]:
        """Parse the case text and return the input and expected return code."""
        h3_matches = list(LEVEL3_REGEX.finditer(case_text))
        n_h3_matches = len(h3_matches)
        if n_h3_matches != 2:
            raise ValueError(
                f"Case {self.title} has {n_h3_matches} level 3 headings. 2 expected."
            )
        if h3_matches[0].group(1) != "Input" or h3_matches[1].group(1) != "Result":
            raise ValueError(
                f"Case {self.title} does not have `Input` and `Result` headings."
            )

        input_section = case_text[h3_matches[0].end() : h3_matches[1].start()]
        result_section = case_text[h3_matches[1].end() : None]

        input_code_blocks = list(CODE_REGEX.finditer(input_section))
        result_code_blocks = list(CODE_REGEX.finditer(result_section))
        if len(input_code_blocks) != 1 or len(result_code_blocks) != 1:
            raise ValueError(
                f"Case {self.title} must have exactly 1 code block per section."
            )

        input_code_type = input_code_blocks[0].group(1)
        input_code = input_code_blocks[0].group(2)
        result_code_type = result_code_blocks[0].group(1)
        result_code = result_code_blocks[0].group(2)
        if input_code_type != self.block_type or result_code_type != self.block_type:
            raise ValueError(
                f"Case {self.title}: each code block must be labeled as "
                f"{self.block_type}"
            )

        # If the code block is empty Regex returns None. Coerce to empty string.
        input_code = "" if input_code is None else input_code.strip()
        result_code = "" if result_code is None else result_code.strip()
        return input_code, result_code


def find_test_cases(path: str, block_type: str) -> list[MdTestCase]:
    """Return all test cases in the markdown file.

    :param path: Path to the markdown file containing cases.
    :param block_type: The label for each triple backtick code block.

    :return: A list of MdTestCase objects for each case.
    """
    with open(path, "r", encoding="utf-8") as file_obj:
        text = file_obj.read()
    test_case_headings = list(CASE_REGEX.finditer(text))
    test_cases = []
    n_headings = len(test_case_headings)
    for i, heading_match in enumerate(test_case_headings):
        title = heading_match.group(1)
        start = heading_match.end()
        end = test_case_headings[i + 1].start() if i + 1 < n_headings else None

        case_text = text[start:end]
        test_cases.append(MdTestCase(title, case_text, block_type))
    return test_cases

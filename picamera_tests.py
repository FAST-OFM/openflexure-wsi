#! /usr/bin/env python3
"""Run the Piamera tests and archive the results in the git repository."""

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
import zipfile

CAM_DIR = os.path.join("src", "openflexure_microscope_server", "things", "camera")
ZIP_FILE = "picamera_coverage.zip"
COVERAGE_FILE = ".coverage.picamera"
HASH_FILE = "picamera_test_hashes.json"

# If any of the HASHED_FILES change, then the picamera tests need to be re-run.
# System independent can be checked on windows even if running tests must be on a Pi
HASHED_FILES = [
    os.path.join(CAM_DIR, "__init__.py"),
    os.path.join(CAM_DIR, "picamera.py"),
    os.path.join(CAM_DIR, "picamera_recalibrate_utils.py"),
    os.path.join(CAM_DIR, "picamera_tuning_file_utils.py"),
]


def _get_hashes(include_coverage: bool = False) -> dict[str, str]:
    """Return a dictionary of Git hashes for the HASHED FILES.

    The key is the posixpath to the file so matching works even if the dictionary is
    compared to a dictionary created on another system.

    The coverage file is hashed when running for debug purposes.

    Git hashes are so the files are line ending independent.
    """
    hashes = {}
    filepaths = HASHED_FILES
    if include_coverage:
        filepaths.append(COVERAGE_FILE)
    for filepath in filepaths:
        if not os.path.isfile(filepath):
            # Sys exit rather than raise for better command line experience
            print(f"ERROR: {filepath} does not exist!")
            sys.exit(-1)
        # Create git hashes for file system independence
        file_hash = subprocess.check_output(["git", "hash-object", filepath])
        hashes[posixpath.normpath(filepath)] = file_hash.decode("utf-8").strip()
    return hashes


def run_tests(exitfirst: bool = False) -> None:
    """Run the picamera tests, zip the coverage database, hash relevant source files."""
    pytest_cmd = ["pytest"]

    if exitfirst:
        pytest_cmd.append("-x")

    pytest_cmd.append("tests/hardware_specific_tests")
    subprocess.run(pytest_cmd, check=True)

    shutil.copyfile(".coverage", COVERAGE_FILE)
    hash_dict = _get_hashes(include_coverage=True)
    with open(HASH_FILE, "w", encoding="utf-8") as json_file:
        json.dump(hash_dict, json_file)
    with zipfile.ZipFile(ZIP_FILE, "w") as zipf:
        zipf.write(COVERAGE_FILE)
        zipf.write(HASH_FILE)


def unzip_coverage() -> None:
    """Unzip coverage database if hashes match relevant source files."""
    if zipfile.is_zipfile(ZIP_FILE):
        with zipfile.ZipFile(ZIP_FILE, "r") as zip_obj:
            zip_obj.extractall()
    else:
        print("ERROR: Zipfile with coverage results not found.")
        sys.exit(-1)

    hash_dict = _get_hashes()

    if os.path.isfile(HASH_FILE):
        with open(HASH_FILE, "r", encoding="utf-8") as json_file:
            zipped_hash_dict = json.load(json_file)
    else:
        print("ERROR: JSON hashes not available. Zipfile is incomplete.")
        sys.exit(-1)
    del zipped_hash_dict[COVERAGE_FILE]

    if hash_dict != zipped_hash_dict:
        # Sys exit rather than raise for better command line experience
        print(
            "ERROR: hashes don't match. This probably means the source has changed "
            "since the tests were last run on the Pi."
        )
        sys.exit(-1)


def main() -> None:
    """Either run tests or unzip previous results."""
    parser = argparse.ArgumentParser(
        description=(
            "picamera_tests: run or validate PiCamera hardware test coverage.\n\n"
            "Use 'run' on the Pi to execute tests and package coverage data.\n"
            "Use 'unzip' on another machine to extract and verify that the test\n"
            "data matches the source code before unzipping it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run hardware-specific tests on the PiCamera",
    )

    subparsers.add_parser(
        "unzip", help="Unzip and verify coverage data on another machine"
    )
    run_parser.add_argument(
        "-x",
        "--exitfirst",
        action="store_true",
        help="Stop on first test failure",
    )

    args = parser.parse_args()

    if args.command == "run":
        run_tests(exitfirst=args.exitfirst)
    elif args.command == "unzip":
        unzip_coverage()


if __name__ == "__main__":
    main()

"""Tests that version strings can be produced reliably."""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import warnings

import pytest
from hypothesis import strategies as st
from hypothesis.errors import NonInteractiveExampleWarning

from openflexure_microscope_server import utilities

# Useful for really finding the repo dir when we mock where it is.
THIS_DIR = os.path.dirname(__file__)
TRUE_REPO_DIR = os.path.dirname(os.path.dirname(THIS_DIR))

# For explicit version checking.
VER_STRING = "3.0.0-beta1"

# This is the regex provided by https://semver.org/
SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Use hypothesis to generate random filenames
FILE_NAME_GEN = st.from_regex(r"[a-zA-Z_-]{5,20}\.[a-z]{1,5}", fullmatch=True)
# Use hypothesis to generate random data for files
FILE_DATA_GEN = st.from_regex(r"[a-zA-Z_-]{5,20}\.[a-z]{1,5}", fullmatch=True)


def create_fake_file() -> None:
    """Create a file with a random name and random content in the working directory."""
    with warnings.catch_warnings():
        # Ignore hypothesis warning as we are not using for hypothesis testing but just
        #  as a convenient way to create random files for Git.
        warnings.filterwarnings("ignore", category=NonInteractiveExampleWarning)

        fname = FILE_NAME_GEN.example()
        with open(fname, "w", encoding="utf-8") as file_obj:
            file_obj.write(FILE_DATA_GEN.example())


def _git(command: str) -> str:
    """Run a git command in a subprocess."""
    git_command = ["git"] + command.split()
    git_env = os.environ.copy()
    git_env.update(
        {
            "GIT_AUTHOR_NAME": "OpenFlexure test",
            "GIT_AUTHOR_EMAIL": "openflexure-test@example.invalid",
            "GIT_COMMITTER_NAME": "OpenFlexure test",
            "GIT_COMMITTER_EMAIL": "openflexure-test@example.invalid",
        }
    )
    # Use check=True so an error is raised if git has a problem. Only read STDOUT
    result = subprocess.run(git_command, check=True, capture_output=True, env=git_env)
    return result.stdout.decode().strip()


@pytest.fixture
def temp_dir():
    """Return the path of a temporary directory (set as working dir)."""
    working_dir = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            os.chdir(tmpdir)
            yield tmpdir
        finally:
            # Change back to working dir before closing context manager or Windows will error
            os.chdir(working_dir)


@pytest.fixture
def git_repo(temp_dir):
    """Return a git repo path (set as working dir) with couple of commits."""
    # Initialise, create files and commit a couple of times
    _git("init")
    create_fake_file()
    create_fake_file()
    _git("add -A")
    _git("commit -m 'message'")
    create_fake_file()
    create_fake_file()
    _git("add -A")
    _git("commit -m 'message2'")

    # Yielding here as tempdir is yielding and we want to stay in the tempdir
    # context manager. Silencing ruff saying it should be return.
    yield temp_dir  # noqa: PT022


def test_version_data_git_and_toml(mocker, git_repo):
    """Check expected version reported if git repo and pyproject.toml are available."""
    # Patch the variable for this repo to the repo provided by the fixture.
    mocker.patch.object(utilities, "REPO_DIR", new=git_repo)
    git_hash = _git("rev-parse HEAD")
    shutil.copy(os.path.join(TRUE_REPO_DIR, "pyproject.toml"), git_repo)
    version_data = utilities.robust_version_strings()
    # Check versions are as expected. Prepending the v to the semantic version.
    assert version_data.version == "v" + VER_STRING
    assert version_data.version_source == git_hash


def test_version_data_toml_only(mocker, temp_dir):
    """In the case of a pyproject.toml but not git dir, check version is reported."""
    # Patch the variable for this repo to the temp dir provided by the fixture.
    mocker.patch.object(utilities, "REPO_DIR", new=temp_dir)
    shutil.copy(os.path.join(TRUE_REPO_DIR, "pyproject.toml"), temp_dir)
    version_data = utilities.robust_version_strings()
    # Check versions are as expected. Prepending the v to the semantic version.
    assert version_data.version == "v" + VER_STRING
    assert version_data.version_source == "TOML"


def test_version_data_git_only(mocker, git_repo, caplog):
    """In the odd case of a git repo and no pyproject.toml. Check the hash is reported.

    The version string should just be reported as "Undefined". This is weird, so we
    expect there to be errors logged.
    """
    mocker.patch.object(utilities, "REPO_DIR", new=git_repo)
    with caplog.at_level(logging.ERROR):
        git_hash = _git("rev-parse HEAD")
        version_data = utilities.robust_version_strings()
        # Check versions are as expected. Prepending the v to the semantic version.
        assert version_data.version == "Undefined"
        assert version_data.version_source == git_hash
        # There should be 1 error level log
        assert len(caplog.records) == 1


def test_version_distribution(mocker, temp_dir):
    """Simulate a distribution package with no pyproject.toml or git directory.

    Check the returned result is from importlib.
    """
    mocker.patch.object(utilities, "REPO_DIR", new=temp_dir)
    # As `openflexure_microscope_server.utilities` imported `version` from
    # `importlib.metadata` to mock the already imported:
    # `openflexure_microscope_server.utilities.version`, this is `version` from
    # `importlib.metadata`
    mocker.patch(
        "openflexure_microscope_server.utilities.version", return_value="oobar"
    )
    version_data = utilities.robust_version_strings()
    # Expect the version is the output from importlib.metadata.version, prepended by a
    # "v". So, voobar, as we mocked the version to be oobar.!
    assert version_data.version == "voobar"
    assert version_data.version_source == "Dist"


def test_reading_hash_from_git():
    """Use create a Git repo and check that hash can be read.

    The performs a series of directory and git operations, commented as they go.
    """
    working_dir = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            git_dir = os.path.join(tmpdir, ".git")
            # Git dir not created yet. Returns Undefined
            assert utilities._get_hash_from_git_dir(git_dir) == "Undefined"

            # Initialised. Should still be undefined.
            _git("init")
            assert utilities._get_hash_from_git_dir(git_dir) == "Undefined"

            # Create some fake files and commit them.
            create_fake_file()
            create_fake_file()
            _git("add -A")
            _git("commit -m 'message'")
            git_hash1 = _git("rev-parse HEAD")
            # Check we read the hash the same as the git executable
            assert utilities._get_hash_from_git_dir(git_dir) == git_hash1

            # Create more fake files and check again.
            create_fake_file()
            create_fake_file()
            _git("add -A")
            _git("commit -m 'message2'")
            git_hash2 = _git("rev-parse HEAD")
            assert utilities._get_hash_from_git_dir(git_dir) == git_hash2

            # Save the branch name (robust to the default branch name changing)
            branch_name = _git("rev-parse --abbrev-ref HEAD")

            # Check out the old commit in detached HEAD and check it is correct
            _git(f"checkout {git_hash1}")
            assert utilities._get_hash_from_git_dir(git_dir) == git_hash1

            # Check out the branch and check commit changed back
            _git(f"checkout {branch_name}")
            assert utilities._get_hash_from_git_dir(git_dir) == git_hash2
            # Change back to working dir before closing context manager or Windows will error
            os.chdir(working_dir)
    finally:
        # Ensure changed to working dir
        os.chdir(working_dir)


def test_reading_hash_from_broken_git_dir(git_repo):
    """Break a git dir and check the hash is "Undefined"."""
    git_dir = os.path.join(git_repo, ".git")

    # Break the git directory and check the hash is undefined
    shutil.move(
        os.path.join(git_dir, "refs"),
        os.path.join(git_dir, "refs1"),
        copy_function=shutil.copytree,
    )
    assert utilities._get_hash_from_git_dir(git_dir) == "Undefined"

    # Fix it and check it works again
    shutil.move(
        os.path.join(git_dir, "refs1"),
        os.path.join(git_dir, "refs"),
        copy_function=shutil.copytree,
    )

    commit_hash = utilities._get_hash_from_git_dir(git_dir)
    assert utilities.COMMIT_REGEX.match(commit_hash) is not None

    # Break the git directory differently and check the hash is undefined
    os.remove(os.path.join(git_dir, "HEAD"))
    assert utilities._get_hash_from_git_dir(git_dir) == "Undefined"


def test_reading_hash_from_broken_git_ref_file(git_repo):
    """Break the git ref file and check the hash is "Undefined"."""
    git_dir = os.path.join(git_repo, ".git")
    branch_name = _git("rev-parse --abbrev-ref HEAD")
    ref_path = os.path.join(git_dir, "refs", "heads", branch_name)
    # Check commit can be read
    commit_hash = utilities._get_hash_from_git_dir(git_dir)
    assert utilities.COMMIT_REGEX.match(commit_hash) is not None
    # Write something into the ref file
    with open(ref_path, "w", encoding="utf-8") as head_file:
        head_file.write("This is now broken")
    # It is now undefined
    assert utilities._get_hash_from_git_dir(git_dir) == "Undefined"


def test_reading_hash_from_broken_git_head_file(git_repo):
    """Break the git HEAD file and check the hash is "Undefined"."""
    git_dir = os.path.join(git_repo, ".git")
    head_path = os.path.join(git_dir, "HEAD")
    # Check commit can be read
    commit_hash = utilities._get_hash_from_git_dir(git_dir)
    assert utilities.COMMIT_REGEX.match(commit_hash) is not None
    # Write something into the ref file
    with open(head_path, "w", encoding="utf-8") as ref_file:
        ref_file.write("This is now broken")
    # It is now undefined
    assert utilities._get_hash_from_git_dir(git_dir) == "Undefined"


def test_reading_version_from_toml():
    """Check version string can be read from this project's TOML file.

    As we only expect to ever read our own TOML file there is not a need to do
    excessive testing on different generated TOML files.
    """
    project_toml_path = os.path.join(utilities.REPO_DIR, "pyproject.toml")
    ver_string = utilities._get_version_from_toml(project_toml_path)
    # Check version does follow semantic versioning.
    assert SEMVER_REGEX.match(ver_string)
    # Explicit check
    assert ver_string == VER_STRING


def test_error_reading_version_from_toml():
    """Check Undefined is returned if error reading TOML file."""
    # Just give it the wrong path.
    project_toml_path = os.path.join(utilities.REPO_DIR, "pyppproject.toml")
    ver_string = utilities._get_version_from_toml(project_toml_path)
    # Explicit check
    assert ver_string == "Undefined"

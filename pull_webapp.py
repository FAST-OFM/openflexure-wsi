#! /usr/bin/env python3

"""Download the server static files from the GitLab CI.

This script downloads the latest build artifact from a specified branch in the GitLab
repository and extracts its contents into the server static directory within the local
source tree.

If the static directory already exists and contains data, it will be removed before
extraction.
"""

import argparse
import io
import os
import shutil
import sys
import zipfile
from typing import Optional

import gitlab
from gitlab.exceptions import GitlabGetError

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(THIS_DIR, "src", "openflexure_microscope_server", "static")


def remove_static_if_exists() -> None:
    """Remove the existing 'static' directory if it exists and contains any files."""
    if os.path.exists(STATIC_DIR) and os.listdir(STATIC_DIR):
        shutil.rmtree(STATIC_DIR)
        print(f"Removed existing directory: {STATIC_DIR}")


def extract_zip(zip_data: bytes) -> None:
    """Extract a zip archive from in-memory bytes to the openflexure repo root.

    :param zip_data: The zip file content as bytes.
    """
    zip_stream = io.BytesIO(zip_data)
    with zipfile.ZipFile(zip_stream, "r") as zip_file:
        zip_file.extractall(THIS_DIR)
        print(f"Extracted zip to: {THIS_DIR}")


def main(branch: str = "v3", project: Optional[str] = None) -> None:
    """Fetch build artifacts from GitLab for a given branch and unzip.

    Clear any existing static directory before extract the new files.

    :param branch: The Git branch name to fetch artifacts from.
    :param project: The GitLab project ID if pulling from a fork. Use None for
        pulling from the main openflexure GitLab.
    """
    project_id = 9238334 if project is None else int(project)
    try:
        gl = gitlab.Gitlab()
        ofm_repo = gl.projects.get(project_id)

        zip_data = ofm_repo.artifacts.download(branch, "build")
    except GitlabGetError as e:
        print(
            "Failed to get artifacts from GitLab.\n"
            "If pointing to a branch, ensure the artifacts haven't expired,\n"
            f"and rerun the pipeline from Gitlab if necessary.\n{e}"
        )
        sys.exit(-1)

    if zip_data is None:
        print("Latest pipeline has no build artifact.")
        sys.exit(-1)
    remove_static_if_exists()
    extract_zip(zip_data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download the server static files from the GitLab CI."
    )
    parser.add_argument(
        "-b",
        "--branch",
        default="v3",
        help="Git branch to fetch artifacts from (default: v3)",
    )
    parser.add_argument(
        "-p",
        "--project",
        default=None,
        help="The GitLab project ID if using a fork",
    )

    args = parser.parse_args()
    main(branch=args.branch, project=args.project)

#! /usr/bin/env python3

"""Create a list of the MRs into v3 (unless specified) since the last release."""

import argparse
import sys

import gitlab
from gitlab.exceptions import GitlabGetError

PROJECT_ID = 9238334


def main(tag: str, branch: str = "v3") -> None:
    """Create a list of the MRs into a given branch after a tag.

    :param tag: The tag the mrs should be since.
    :param branch: The Git branch name MRs were merged into.
    """
    try:
        gl = gitlab.Gitlab()
        ofm_repo = gl.projects.get(PROJECT_ID)
        tag_obj = ofm_repo.tags.get(tag)
        tag_date = tag_obj.commit["committed_date"]
        # There is no "merged_after option". So get everything updated after
        relevant_mrs = ofm_repo.mergerequests.list(
            state="merged",
            target_branch=branch,
            order_by="merged_at",
            updated_after=tag_date,
            get_all=True,
        )
        # Filter for those merged after the tagged commit.
        for mr in reversed(relevant_mrs):
            if mr.merged_at > tag_date:
                print(f"* !{mr.iid} {mr.title}")
    except GitlabGetError as e:
        print(f"Failed contact gitlab server.\n{e}")
        sys.exit(-1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Get Merge requests since a specific tag to help with creating a "
            "changelog."
            "The response is printed to the terminal."
            "Check the start of the list, it may have a couple of extra MRs on the "
            "same day as the tag."
        )
    )
    parser.add_argument("tag", help="The Git tag to start looking from (required)")
    parser.add_argument(
        "-b",
        "--branch",
        default="v3",
        help="Git branch that requests were merged into (default: v3)",
    )

    args = parser.parse_args()
    main(tag=args.tag, branch=args.branch)

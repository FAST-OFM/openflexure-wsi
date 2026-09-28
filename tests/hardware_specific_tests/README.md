# Hardware specific tests

This directory contains unit tests that require specific hardware to run. Current they implement tests for code that interacts with the Raspberry Pi Camera.

## Hardware specific tests for the Pi Camera.

The OpenFlexure Microscope Server runs automated test in the cloud (via the GitLab CI) to test each version of the code as changes are proposed and merged. As these tests run in the cloud they do not have access to actual microscope hardware. As such, these tests either test specific blocks of code that don't need hardware access or make use of simulated hardware via the simulation camera and dummy stage.

To test the code that interacts with the hardware itself, hardware specific tests are needed and they must be run with the hardware available. This directory provides these tests. For now the only hardware specific tests are for the Pi Camera. They must be run on a Raspberry Pi with a Pi Camera attached.

In GitLab we track our "code coverage", this tells us which lines of code have been executed during testing. The `picamera_tests.py` script in the root of the repository can be used to report the coverage from these tests. See the section below on "Reporting the coverage to GitLab".

### Running and reporting the test coverage to GitLab (for merge requests)

It is essential to stop the server before running these tests:

    ofm stop

To create a coverage report that will be included into the repository (and reported to GitLab) run:

    ./picamera_tests.py run

This will run every hardware test, recording their success or failure. As these tests are slow to run, it may be useful to set it to exit on first failure. To do this, run the above command with ` -x` at the end.

This will first run `pytest` on the hardware specific tests. This creates a `.coverage` database that can be used to analyse the code coverage of these tests. This will be copied to `.coverage.picamera`. The script will then create a zip of these test results along with hashes of source code for the Pi Camera:

* `picamera.py`
* `picamera_recalibrate_utils.py`
* `__init__.py` in the `camera` directory that defines `BaseCamera`

This zip should then be committed to the repository.

### Running these tests during development

As before, the server must be stopped before running these tests.

The camera test are very slow as they run on hardware. They can be run with:

    pytest tests/hardware_specific_tests

However, this will not archive the tests in the Git repository for reporting the coverage. For this, see the section above on reporting the coverage.

When writing and debugging these unit tests, it is often best to run a specific test and to use the `-s` flag to see the print statements. It is also often useful to use `--pdb` to drop you into a python debug session on any failure. For example, you might run:

    pytest tests/hardware_specific_tests/picamera2/test_exposure_time_drift.py::test_exposure_time_saves_and_loads -s --pdb

### CI explanation

When the CI runs on GitLab to calculate code coverage, it will first run the tests in the `tests` directory in one job, and archive this as `.coverage.main`. Then it will run a second job that:

* Imports the archive of `.coverage.main` for the tests just run on the server
* Unzip the zip of the results of running tests on the Pi Camera (`.coverage.picamera`)
* Check that the hashes for the Pi Camera source code have not changed. If they have changed it will error and ask for the picamera tests to be re-run on a Raspberry Pi.
* Runs `coverage combine` to create a single `.coverage` report that combines the coverage from both `.coverage.main` and `.coverage.picamera`.
* Generates the information needed to display the coverage in GitLab

This ensures that:

* Hardware specific tests are re-run if the relevant source code changes.
* The coverage for hardware specific tests is reported correctly
* The hardware specific tests do not need re-running when other code that does not affect PiCamera interaction is updated.

### Keeping your branch up to date

One of the downsides of this form of testing is that if both the target and the source branch have changes to the camera files, then the merged result will fail on CI. This is because the tests were not run against the merged result.

As such, any branches making camera changes need to kept in sync with their target branch. It is preferable to do this with rebasing, to prevent a very confusing series of merge commits.

To rebase your branch, first make sure it is checked out by running (with BRANCH_NAME updated):

`git checkout BRANCH_NAME`

Make sure your computer knows the server's current state by fetching:

`git fetch`

Then run the rebase (assuming `v3` is the target branch):

`git rebase origin/v3`

**Note:** If both branches have updated the zip, you may have a `both modified` conflict on the zip itself. The best way to resolve this is to just to use the file from the branch you are rebasing on. You can do this by running the slightly confusing command of:

`git checkout --ours -- picamera_coverage.zip`

if you then run:

`git add -A`  
`git status`

You should see that there is now no conflict and that `picamera_coverage.zip` is not listed as modified at all. To carry on rebasing run:

`git rebase --continue`

Once done you can push your rebased changes with:

`git push --force-with-lease`

Do **not** just run `git push` and then follow the instructions to first "pull". This can create a horrible mess.


**If you want to use a branch that has been rebased on another computer.**

If you try to pull this branch on another computer that already had an older version checked out, you will get a warning about the force push. In this case, we want the server's history exactly and want to forget the history on that computer.

**First:** Check you have no unsaved work on this branch, as we are about to delete it!

Then you can replace the local history by running (with BRANCH_NAME updated):

`git reset --hard origin/BRANCH_NAME`


### Creating a combined report locally

To create a combined coverage report locally (similar to the one created on CI) first run all normal tests with `pytest`.

Copy the `.coverage` file to `.coverage.main`

    cp .coverage .coverage.main

Then run:

    ./picamera_tests.py unzip

to get the `.coverage.picamera` file.

To combine reports run:

    coverage combine

It is then possible to run:

* `coverage report` to get the in-terminal report of the coverage.
* `coverage html` to get the HTML overview of covered lines.
* `coverage xml` to get an XML report any software that may need it.

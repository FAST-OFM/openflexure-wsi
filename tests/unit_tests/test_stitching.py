"""Test that the code that talks to the external stitching process acts as expected.

This does not actually run stitching. Instead it checks that the expected commands are
generated, and the subprocess calling works as expected.
"""

import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

import labthings_fastapi as lt

from openflexure_microscope_server.stitching.stitching import (
    FORBIDDEN_COMMANDS,
    BaseStitcher,
    FinalStitcher,
    PreviewStitcher,
    StitcherValidationError,
    StitchingProcessSettings,
    StitchingSettings,
    validate_command,
)

from ..shared_utils.lt_test_utils import LabThingsTestEnv

# A global logger pretending to the logger from a thing
LOGGER = logging.getLogger("mock-thing_logger")
FAKE_DIR: list[str] = os.path.join("a", "dir", "that", "is", "fake")
THIS_DIR: str = os.path.dirname(os.path.realpath(__file__))
MOCK_STITCHER: str = os.path.join(THIS_DIR, "mock_stitching", "mock-stitch.py")


def test_validate_command_success():
    """Test valid commands pass validation."""
    validate_command(["openflexure-stitch", "--stitching_mode", "all", "path/to/scan"])
    validate_command(["--resize", "0.5", "8192"])


@pytest.mark.parametrize("cmd_name", FORBIDDEN_COMMANDS)
def test_validate_command_forbidden(cmd_name):
    """Test forbidden commands raise error."""
    # As the main command
    with pytest.raises(
        StitcherValidationError, match=f"Forbidden element '{cmd_name}' detected"
    ):
        validate_command([cmd_name, "some_arg"])

    # As an argument (case-insensitive)
    with pytest.raises(
        StitcherValidationError,
        match=f"Forbidden element '{cmd_name.upper()}' detected",
    ):
        validate_command(["safe-command", cmd_name.upper()])


def test_base_stitcher():
    """Test the logic in BaseStitcher.

    Pretty much the only logic in base stitcher is forming a command, and calculating
    the min_overlap from overlap.

    The BaseStitcher can't start as the start method is explicitly NotImplemented.
    """
    # Overlaps and expected minimum overlap to be in command line argument.
    overlaps = [(0.1, "0.09"), (0.4, "0.36"), (0.8, "0.72")]

    for overlap, min_overlap in overlaps:
        expected_command = [
            "openflexure-stitch",
            "--stitching_mode",
            "all",
            "--minimum_overlap",
            min_overlap,
            "--resize",
            "0.5",
            FAKE_DIR,
        ]
        stitcher = BaseStitcher(FAKE_DIR, overlap=overlap, correlation_resize=0.5)
        assert stitcher.command == expected_command


def test_preview_stitcher_command():
    """Check preview stitcher command for a specific example."""
    expected_command = [
        "openflexure-stitch",
        "--stitching_mode",
        "preview_stitch",
        "--minimum_overlap",
        "0.09",
        "--resize",
        "0.5",
        FAKE_DIR,
    ]
    stitcher = PreviewStitcher(FAKE_DIR, overlap=0.1, correlation_resize=0.5)
    assert stitcher.command == expected_command


FINAL_EXPECTED_COMMAND = [
    "fast-ofm-core",
    "run-request",
    os.path.join(FAKE_DIR, "fast-ofm-stitch-request.json"),
    "--artifact-root",
    FAKE_DIR,
]

DEFAULT_SETTINGS = StitchingSettings(correlation_resize=0.5, overlap=0.1)


def test_final_stitcher_command_tiff_default():
    """Final stitching requests direct DZI and BigTIFF pyramids by default."""
    stitcher = FinalStitcher(
        FAKE_DIR, logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )
    assert stitcher.command == FINAL_EXPECTED_COMMAND


def test_final_stitcher_command_without_tiff(caplog):
    """The legacy flag cannot silently suppress the canonical WSI anymore."""
    with caplog.at_level(logging.WARNING):
        stitcher = FinalStitcher(
            FAKE_DIR,
            logger=LOGGER,
            stitching_settings=DEFAULT_SETTINGS,
            stitch_tiff=False,
        )
    assert stitcher.command == FINAL_EXPECTED_COMMAND
    assert "deprecated and ignored" in caplog.text


def test_final_stitcher_prepares_full_core_request(mocker):
    """Resource and registration choices move inside the versioned request."""
    stitcher = FinalStitcher(
        FAKE_DIR,
        logger=LOGGER,
        stitching_settings=StitchingSettings(correlation_resize=0.25, overlap=0.4),
        process_settings=StitchingProcessSettings(
            workers=2, ram_cache_mb=2048, correlation_mode="overlap-strip"
        ),
    )
    writer = mocker.patch(
        "openflexure_microscope_server.stitching.stitching.write_stitching_request",
        return_value=Path(FAKE_DIR) / "fast-ofm-stitch-request.json",
    )

    stitcher._prepare_core_request()

    writer.assert_called_once_with(
        Path(FAKE_DIR),
        output_name="fake_stitched",
        workers=2,
        cache_bytes=2048 * 1024 * 1024,
        registration_mode="experimental_overlap_strip",
        minimum_overlap=0.36,
        correlation_resize=0.25,
        work_tile_size=8192,
        viewer_dzi=True,
    )


def _validation_error_tester(scan_path, **kwargs):
    """Check stitcher throws a validation error for the given init args."""
    with pytest.raises(StitcherValidationError):
        BaseStitcher(scan_path, **kwargs).command
    with pytest.raises(StitcherValidationError):
        PreviewStitcher(scan_path, **kwargs).command


def test_validation_error():
    """Test a number of ways to try to inject malicious arguments into the stitcher.

    The stitcher should throw a validation error each attempt.
    """
    # Tests for preview (and base) stitcher
    _validation_error_tester("/dir;rm -rf /;", overlap=".2", correlation_resize=".25")
    _validation_error_tester(FAKE_DIR, overlap=".2", correlation_resize=".25;rm -rf /;")
    _validation_error_tester(FAKE_DIR, overlap=".2;rm -rf /;", correlation_resize=".25")

    class EvilModel(BaseModel):
        overlap: str
        correlation_resize: str

    with pytest.raises(StitcherValidationError):
        FinalStitcher(
            FAKE_DIR,
            logger=LOGGER,
            stitching_settings=EvilModel(
                overlap=".2;rm -rf /;", correlation_resize=".25"
            ),
        )


def test_extra_arg_validation():
    """Test that malicious arguments in extra_args also throw validation error.

    Currently extra args do not come from user input. But this makes checks more
    future-proof.
    """
    stitcher = FinalStitcher(
        FAKE_DIR, logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )
    stitcher._extra_args = ["&&rm -rf /&&"]
    with pytest.raises(StitcherValidationError):
        stitcher.command


def test_preview_stitching_command(caplog, mocker):
    """Check the preview process runs in a background thread and doesn't log."""
    mock_cmd = f"{sys.executable} {MOCK_STITCHER}"

    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.STITCHING_CMD", mock_cmd
    )

    with caplog.at_level(logging.INFO):
        stitcher = PreviewStitcher(FAKE_DIR, overlap=0.1, correlation_resize=0.5)
        stitcher.start()
        # Should take a second or so to run so will still be running
        assert stitcher.running
        # Can't start another time, instead get a runtime error
        with pytest.raises(RuntimeError):
            stitcher.start()
        # Wait for it to complete
        stitcher.wait()
        # It is now not running
        assert not stitcher.running
        assert len(caplog.records) == 0


class StitchingTestThing(lt.Thing):
    """A Thing for running stitching in invocation threads.

    This is needed to check cancellation behaviour.
    """

    @lt.action
    def run_preview(self):
        """Run the preview stitcher."""
        stitcher = PreviewStitcher(FAKE_DIR, overlap=0.1, correlation_resize=0.5)
        # Send in the argument HANG to mock-stitch and it just hang for 10s
        stitcher._extra_args = ["HANG"]
        stitcher.start()
        stitcher.wait()

    @lt.action
    def run_final(self):
        """Run the final stitcher."""
        stitcher = FinalStitcher(
            FAKE_DIR, logger=self.logger, stitching_settings=DEFAULT_SETTINGS
        )
        # Send in the argument HANG to mock-stitch and it just hang for 10s
        stitcher._extra_args = ["HANG"]
        stitcher.run()


@pytest.fixture
def stitching_test_env():
    """Return a test environment for a server with just StitchingTestThing."""
    with LabThingsTestEnv(things={"stitcher": StitchingTestThing}) as env:
        yield env


def test_preview_stitching_cancelled(stitching_test_env, mocker):
    """Check that preview stitch can be cancelled."""
    mock_cmd = f"{sys.executable} {MOCK_STITCHER}"

    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.STITCHING_CMD", mock_cmd
    )

    t_start = time.time()
    # Start the action
    response = stitching_test_env.start_action("stitcher", "run_preview")
    # Sleep long enough for at least 1 log.
    time.sleep(0.5)
    # Cancel using a DELETE request
    stitching_test_env.cancel_action(response)
    invocation_data = stitching_test_env.poll_action(response)

    # If it wasn't cancelled it would hang for 10 s. Here we check the cancel killed
    # it within 2s.
    assert time.time() - t_start < 2
    assert invocation_data["status"] == "cancelled"
    logs = invocation_data["log"]
    assert len(logs) == 1
    assert re.match(r"^Invocation [0-9a-f-]+ was cancelled", logs[0]["message"])


def test_final_stitching_command(caplog, mocker):
    """Check the final stitch runs until completion, and print statements are logged."""
    mock_cmd = f"{sys.executable} {MOCK_STITCHER}"

    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.CORE_STITCHING_CMD", mock_cmd
    )
    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.write_stitching_request",
        return_value=Path(FAKE_DIR) / "fast-ofm-stitch-request.json",
    )
    expected_arguments = FINAL_EXPECTED_COMMAND[1:]

    with caplog.at_level(logging.INFO):
        stitcher = FinalStitcher(
            FAKE_DIR, logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
        )
        # For the final stitcher it will always complete before returning.
        stitcher.run()
        # The mock command logs the inputs (but not the initial command) and the
        # stitcher logs # "Stitching complete" when it ends.
        assert len(caplog.records) == len(expected_arguments) + 1
        for i, record in enumerate(caplog.records):
            msg = record.message.strip()
            if i == len(expected_arguments):
                assert msg == "Stitching complete"
            else:
                assert msg == expected_arguments[i]


def test_final_stitching_command_cancelled(stitching_test_env, mocker):
    """Check that final stitch can be cancelled."""
    mock_cmd = f"{sys.executable} {MOCK_STITCHER}"

    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.CORE_STITCHING_CMD", mock_cmd
    )
    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.write_stitching_request",
        return_value=Path(FAKE_DIR) / "fast-ofm-stitch-request.json",
    )

    # Start the action
    response = stitching_test_env.start_action("stitcher", "run_final")
    # Sleep long enough for at least 1 log.
    time.sleep(0.5)
    # Cancel using a DELETE request
    stitching_test_env.cancel_action(response)
    invocation_data = stitching_test_env.poll_action(response)

    assert invocation_data["status"] == "cancelled"
    logs = invocation_data["log"]
    assert len(logs) < len(FINAL_EXPECTED_COMMAND) + 1
    assert logs[-2]["message"] == "Stitching cancelled by user"
    assert re.match(r"^Invocation [0-9a-f-]+ was cancelled", logs[-1]["message"])


def test_final_stitching_command_error(mocker):
    """Check that ChildProcessError is raised if the final stitch errors."""
    mock_cmd = f"{sys.executable} {MOCK_STITCHER}"

    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.CORE_STITCHING_CMD", mock_cmd
    )
    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.write_stitching_request",
        return_value=Path(FAKE_DIR) / "fast-ofm-stitch-request.json",
    )

    stitcher = FinalStitcher(
        FAKE_DIR, logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )
    # Send in the argument ERROR to mock-stitch and it will raise an error rather
    # than echo.
    stitcher._extra_args = ["ERROR"]
    with pytest.raises(ChildProcessError):
        stitcher.run()


def test_final_stitcher_uses_scan_disk_for_temporary_files(tmp_path, mocker):
    """Keep large libvips intermediates off a potentially small system tmpfs."""
    images_dir = tmp_path / "scan" / "images"
    images_dir.mkdir(parents=True)
    stitcher = FinalStitcher(
        str(images_dir), logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )
    process = mocker.MagicMock()
    process.stdout.fileno.return_value = 1
    process.wait.return_value = 0
    popen = mocker.patch(
        "openflexure_microscope_server.stitching.stitching.subprocess.Popen",
        return_value=process,
    )
    mocker.patch("openflexure_microscope_server.stitching.stitching.os.set_blocking")
    mocker.patch.object(stitcher, "_log_ongoing", return_value=[])

    assert stitcher._run_command(stitcher.command) == (0, "")

    environment = popen.call_args.kwargs["env"]
    temporary_directory = environment["TMPDIR"]
    assert os.path.dirname(temporary_directory) == str(images_dir.parent)
    assert environment["TMP"] == temporary_directory
    assert environment["TEMP"] == temporary_directory
    assert environment["VIPS_CONCURRENCY"] == "3"
    assert environment["OMP_NUM_THREADS"] == "3"
    assert environment["OPENBLAS_NUM_THREADS"] == "3"
    assert environment["MKL_NUM_THREADS"] == "3"
    assert environment["NUMEXPR_NUM_THREADS"] == "3"
    assert popen.call_args.kwargs["start_new_session"] is (os.name != "nt")
    assert not os.path.exists(temporary_directory)


def test_final_stitcher_cancellation_kills_process_group(mocker):
    """Cancellation must stop core and its resource-heavy worker together."""
    if os.name == "nt":
        pytest.skip("POSIX process groups are not available on Windows")
    stitcher = FinalStitcher(
        FAKE_DIR, logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )
    process = mocker.MagicMock()
    process.pid = 4242
    process.poll.return_value = None
    process.stdout.readline.return_value = ""
    sleep = mocker.patch(
        "openflexure_microscope_server.stitching.stitching.lt.cancellable_sleep",
        side_effect=lt.exceptions.InvocationCancelledError,
    )
    killpg = mocker.patch("openflexure_microscope_server.stitching.stitching.os.killpg")

    with pytest.raises(lt.exceptions.InvocationCancelledError):
        stitcher._log_ongoing(process)

    sleep.assert_called_once_with(0.2)
    killpg.assert_called_once_with(4242, signal.SIGKILL)
    process.wait.assert_called_once_with()


def _make_complete_pyramidal_outputs(tmp_path):
    """Create a minimal valid final-output set and return its paths."""
    images_dir = tmp_path / "scan" / "images"
    images_dir.mkdir(parents=True)
    jpeg = images_dir / "scan_stitched.jpg"
    tiff = images_dir / "scan_stitched.ome.tiff"
    dzi = images_dir / "scan_stitched.dzi"
    tile = images_dir / "scan_stitched_files" / "0" / "0_0.jpg"
    tile.parent.mkdir(parents=True)
    for path in (jpeg, tiff, dzi, tile):
        path.write_bytes(b"data")
    return images_dir, jpeg, tiff, dzi, tile


def test_finalizer_removes_legacy_jpeg_after_both_pyramids_validate(tmp_path):
    """A legacy flat JPEG is redundant only after both pyramids are complete."""
    images_dir, jpeg, tiff, dzi, tile = _make_complete_pyramidal_outputs(tmp_path)
    stitcher = FinalStitcher(
        str(images_dir), logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )

    stitcher._finalize_pyramidal_output()

    assert not jpeg.exists()
    assert tiff.exists()
    assert dzi.exists()
    assert tile.exists()


def test_finalizer_preserves_legacy_jpeg_when_tiff_is_missing(tmp_path):
    """A failed TIFF conversion must not discard recoverable legacy output."""
    images_dir, jpeg, tiff, _dzi, _tile = _make_complete_pyramidal_outputs(tmp_path)
    tiff.unlink()
    stitcher = FinalStitcher(
        str(images_dir), logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )

    with pytest.raises(ChildProcessError, match="OME-TIFF"):
        stitcher._finalize_pyramidal_output()

    assert jpeg.exists()


def test_finalizer_preserves_legacy_jpeg_when_dzi_tiles_are_missing(tmp_path):
    """A descriptor without its tiles is not a valid browser pyramid."""
    images_dir, jpeg, _tiff, _dzi, tile = _make_complete_pyramidal_outputs(tmp_path)
    (tile.parents[1] / "vips-properties.xml").write_bytes(b"metadata is not a tile")
    tile.unlink()
    stitcher = FinalStitcher(
        str(images_dir), logger=LOGGER, stitching_settings=DEFAULT_SETTINGS
    )

    with pytest.raises(ChildProcessError, match="missing or empty"):
        stitcher._finalize_pyramidal_output()

    assert jpeg.exists()


def test_stitch_all_scans_continues_after_error(mocker, caplog, smart_scan_thing):
    """Test stitching all continues after an error and logs as expected."""
    # The info as a dictionary. This must Validate as ScanGalleryInfo
    base_info = {
        "duration": 20,
        "number_of_images": 10,
        "stitch_available": False,
        "dzi": None,
        "stitched_jpeg": None,
    }

    scans = [
        mocker.Mock(path="scan1", gallery_info=base_info.copy()),
        mocker.Mock(path="scan2", gallery_info=base_info.copy()),
        mocker.Mock(path="scan3", gallery_info=base_info.copy()),
        mocker.Mock(path="scan4", gallery_info=base_info.copy()),
    ]
    # stitch_all skips any scans with less than 2 images, without logging

    scans[2].gallery_info["number_of_images"] = 1

    # ensure scan data exists for all scans
    scan_data = mocker.Mock()
    scan_data.stitching_settings = StitchingSettings(
        overlap=0.35,
        correlation_resize=0.5,
    )

    def mocked_scan_dir(name):
        scan_dir = mocker.Mock()
        scan_dir.images_dir = os.path.join(name, "images")
        scan_dir.get_scan_data.return_value = scan_data
        return scan_dir

    with smart_scan_thing:
        mocker.patch.object(
            smart_scan_thing,
            "_get_scan_dir",
            side_effect=mocked_scan_dir,
        )
        mocker.patch.object(
            smart_scan_thing.gallery_db_engine,
            "get_all_entries",
            return_value=scans,
        )

    def run_side_effect(self):
        if "scan2" in str(self.images_dir):
            raise ChildProcessError("test_message")

    mocker.patch(
        "openflexure_microscope_server.stitching.stitching.FinalStitcher.run",
        autospec=True,
        side_effect=run_side_effect,
    )

    with caplog.at_level("INFO"):
        smart_scan_thing.stitch_all_scans()

    messages = [r.message for r in caplog.records]

    # scan3 is not logged as there is only 1 image so it is ignored by ``stitch_all_scans``
    assert messages == [
        "Stitching scan1",
        "Stitching scan2",
        "Stitching failed: test_message",
        "Stitching scan4",
    ]

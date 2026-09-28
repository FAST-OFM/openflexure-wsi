"""Test data collection from the Raspberry Picamera."""

import json
from pathlib import Path

import numpy as np
from PIL import Image


def test_quick_capture_size(picamera_client):
    """Check that a jpeg grabbed from the stream is the same size as a quick capture."""
    # Grab a jpeg from the stream
    blob = picamera_client.grab_jpeg()
    mjpeg_frame = Image.open(blob.open())
    # Verify throws an error if there are issues with the image
    mjpeg_frame.verify()
    assert mjpeg_frame.format == "JPEG"

    # Capture a jpeg
    blob = picamera_client.capture(
        capture_mode="quick",
        image_format="jpeg",
        retain_image=True,
    )
    jpeg_capture = Image.open(blob.open())
    jpeg_capture.verify()
    assert jpeg_capture.format == "JPEG"

    # Capture an array
    arrlist = picamera_client.capture_as_array(capture_mode="quick")
    array_main = np.array(arrlist)

    # Verify image sizes are the same
    assert mjpeg_frame.size == jpeg_capture.size
    assert array_main.shape[1::-1] == jpeg_capture.size


def test_format(picamera_client):
    """Check capture format is as requested."""
    # Capture a jpeg
    blob = picamera_client.capture(
        capture_mode="quick",
        image_format="jpeg",
        retain_image=True,
    )
    jpeg_capture = Image.open(blob.open())
    jpeg_capture.verify()
    assert jpeg_capture.format == "JPEG"

    blob = picamera_client.capture(
        capture_mode="quick",
        image_format="png",
        retain_image=True,
    )
    png_capture = Image.open(blob.open())
    png_capture.verify()
    assert png_capture.format == "PNG"


def test_standard_capture_size(picamera_client):
    """Check standard capture mode captures at expected size."""
    # Capture a jpeg
    blob = picamera_client.capture(
        capture_mode="standard",
        image_format="jpeg",
        retain_image=True,
    )
    jpeg_capture = Image.open(blob.open())
    jpeg_capture.verify()
    assert jpeg_capture.size == (1640, 1232)


def test_record_framerate(picamera_client):
    """Check that framerate monitoring creates a valid JSON log with good data."""
    log_file = Path(picamera_client.record_framerate(duration=1.0))

    assert log_file.exists()
    assert log_file.is_file()

    # Ensure file is not empty
    assert log_file.stat().st_size > 0

    # Load JSON
    with open(log_file, "r") as f:
        data = json.load(f)

    assert "summary" in data
    assert "samples" in data

    summary = data["summary"]
    samples = data["samples"]

    assert "total_duration" in summary
    assert "total_frames" in summary
    assert "avg_fps" in summary

    assert summary["total_duration"] > 0
    assert summary["total_frames"] > 0
    assert summary["avg_fps"] > 0

    assert len(samples) > 0

    sample = samples[0]

    assert "timestamp" in sample
    assert "frame_count" in sample
    assert "frame_size_bytes" in sample
    assert "instant_fps" in sample

    assert sample["timestamp"] > 0
    assert sample["frame_count"] > 0
    assert sample["frame_size_bytes"] > 0

    assert samples[-1]["frame_count"] > 0

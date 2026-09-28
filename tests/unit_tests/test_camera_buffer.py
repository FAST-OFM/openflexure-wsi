"""Tests for the built in buffer for the camera."""

from random import randint

import numpy as np
import pytest
from PIL import Image

from openflexure_microscope_server.things.camera import (
    CameraMemoryBuffer,
    NoImageInMemoryError,
)

RANDOM_GENERATOR = np.random.default_rng()


def random_image():
    """Create a random image."""
    imarray = RANDOM_GENERATOR.integers(
        low=0, high=255, size=(100, 100, 3), dtype="uint8"
    )
    return Image.fromarray(imarray)


def random_metadata():
    """Create a misc dictionary to pretend to be metadata."""
    # Not very metadata like, but we are just checking that the same dict it
    # is returned
    return {"a": randint(1, 100), "b": randint(1, 100)}


def test_add_and_get_image():
    """Check images can be captured and retrieved."""
    mem_buf = CameraMemoryBuffer()
    misc_image = random_image()
    buffer_id = mem_buf.add_image(misc_image, random_metadata(), "standard")
    returned_image, _, _ = mem_buf.get_image(buffer_id)
    # It is the same image
    assert misc_image is returned_image
    # It is now removed from memory
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image(buffer_id)


def test_add_and_get_image_twice():
    """Check images can be retrieved twice if remove flag set false."""
    mem_buf = CameraMemoryBuffer()
    misc_image = random_image()
    buffer_id = mem_buf.add_image(misc_image, random_metadata(), "standard")
    returned_image, _, _ = mem_buf.get_image(buffer_id, remove=False)
    # It is the same image
    assert misc_image is returned_image
    # It is still in memory
    returned_image, _, _ = mem_buf.get_image(buffer_id)
    assert misc_image is returned_image
    # It is now removed from memory
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image(buffer_id)


def test_get_without_id():
    """Check images can be captured and retrieved without ID."""
    mem_buf = CameraMemoryBuffer()
    misc_image = random_image()
    mem_buf.add_image(misc_image, random_metadata(), "standard")
    returned_image, _, _ = mem_buf.get_image()
    # It is the same image
    assert misc_image is returned_image
    # It is now removed from memory
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image()


def test_get_two_images():
    """Check two images can be retrieved."""
    mem_buf = CameraMemoryBuffer()
    misc_image1 = random_image()
    misc_image2 = random_image()
    buffer_id1 = mem_buf.add_image(
        misc_image1, random_metadata(), "standard", buffer_max=2
    )
    buffer_id1 = mem_buf.add_image(
        misc_image1, random_metadata(), "standard", buffer_max=2
    )
    buffer_id1 = mem_buf.add_image(
        misc_image1, random_metadata(), "standard", buffer_max=2
    )
    buffer_id2 = mem_buf.add_image(
        misc_image2, random_metadata(), "standard", buffer_max=2
    )
    returned_image1, _, _ = mem_buf.get_image(buffer_id1)
    returned_image2, _, _ = mem_buf.get_image(buffer_id2)
    # Assert they are the same images
    assert misc_image1 is returned_image1
    assert misc_image2 is returned_image2
    # They are removed from memory
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image(buffer_id1)
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image(returned_image2)


def test_get_two_images_without_setting_buffer_size():
    """Check two images can't be retrieved if the buffer size isn't set."""
    mem_buf = CameraMemoryBuffer()
    misc_image1 = random_image()
    misc_image2 = random_image()
    buffer_id1 = mem_buf.add_image(misc_image1, random_metadata(), "standard")
    buffer_id2 = mem_buf.add_image(misc_image2, random_metadata(), "standard")
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image(buffer_id1)
    returned_image2, _, _ = mem_buf.get_image(buffer_id2)
    # Image 2 the expected image
    assert misc_image2 is returned_image2


def test_buffer_size_changing():
    """Check buffer size resets back to 1 when if not set."""
    mem_buf = CameraMemoryBuffer()
    misc_image1 = random_image()
    misc_image2 = random_image()
    misc_image3 = random_image()
    buffer_id1 = mem_buf.add_image(
        misc_image1, random_metadata(), "standard", buffer_max=3
    )
    buffer_id2 = mem_buf.add_image(
        misc_image2, random_metadata(), "standard", buffer_max=3
    )
    # Third capture doesn't set buffer size, so it will be reset
    buffer_id3 = mem_buf.add_image(misc_image3, random_metadata(), "standard")
    # As buffer size was reset, images 1 and 2 are deleted
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image(buffer_id1)
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image(buffer_id2)
    returned_image3, _, _ = mem_buf.get_image(buffer_id3)
    # Image 3 is the expected image
    assert misc_image3 is returned_image3


def test_capture_two_images_get_without_id():
    """Check that all images are deleted when getting without id."""
    mem_buf = CameraMemoryBuffer()
    misc_image1 = random_image()
    misc_image2 = random_image()
    mem_buf.add_image(misc_image1, random_metadata(), "standard", buffer_max=2)
    mem_buf.add_image(misc_image2, random_metadata(), "standard", buffer_max=2)
    returned_image, _, _ = mem_buf.get_image()
    # When buffer_id is not specified, the most recent image (image2) is expected to
    # be retrieved
    assert returned_image is misc_image2
    # Check all images were wiped from memory, but trying get_image without an id
    with pytest.raises(NoImageInMemoryError):
        mem_buf.get_image()


def test_buffer_size_respected():
    """Capture 10 images with a buffer size of 5. Check only last 5 exist."""
    mem_buf = CameraMemoryBuffer()

    images = []
    buffer_ids = []
    for _i in range(10):
        image = random_image()
        buffer_id = mem_buf.add_image(
            image, random_metadata(), "standard", buffer_max=5
        )
        images.append(image)
        buffer_ids.append(buffer_id)

    for i, (image, buffer_id) in enumerate(zip(images, buffer_ids, strict=True)):
        if i < 5:
            with pytest.raises(NoImageInMemoryError):
                mem_buf.get_image(buffer_id)
        else:
            returned_image, _, _ = mem_buf.get_image(buffer_id)
            assert image is returned_image


def test_clear_buffer():
    """Capture 10 images clear the buffer and check they are gone."""
    mem_buf = CameraMemoryBuffer()

    images = []
    buffer_ids = []
    for _i in range(10):
        image = random_image()
        buffer_id = mem_buf.add_image(
            image, random_metadata(), "standard", buffer_max=10
        )
        images.append(image)
        buffer_ids.append(buffer_id)

    # Clear the buffer
    mem_buf.clear()

    # They are now gone
    for _image, buffer_id in zip(images, buffer_ids, strict=True):
        with pytest.raises(NoImageInMemoryError):
            mem_buf.get_image(buffer_id)


def test_get_metadata_too():
    """Capture 10 images with metadata and check metadata is returned as expected."""
    mem_buf = CameraMemoryBuffer()

    images = []
    metadatas = []
    buffer_ids = []
    for _i in range(10):
        image = random_image()
        metadata = random_metadata()
        buffer_id = mem_buf.add_image(image, metadata, "standard", buffer_max=10)
        images.append(image)
        metadatas.append(metadata)
        buffer_ids.append(buffer_id)

    # Preallocate zipped data, to avoid long confusing lines
    zipped = zip(images, metadatas, buffer_ids, strict=True)

    # Check both image and metadata
    for image, metadata, buffer_id in zipped:
        returned_image, returned_metadata, _ = mem_buf.get_image(buffer_id)
        assert image is returned_image
        assert metadata is returned_metadata


def test_mode_is_returned():
    """Check that the correct mode name is returned with the image from the buffer."""
    mem_buf = CameraMemoryBuffer()
    misc_image = random_image()
    buffer_id = mem_buf.add_image(misc_image, random_metadata(), "standard")
    _, _, mode = mem_buf.get_image(buffer_id)
    assert mode == "standard"

    buffer_id = mem_buf.add_image(misc_image, random_metadata(), "foobar")
    _, _, mode = mem_buf.get_image(buffer_id)
    assert mode == "foobar"

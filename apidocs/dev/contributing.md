# Contributing to the OpenFlexure Microscope Server

All contributions to the OpenFlexure project should follow our [project contributions guidelines](https://openflexure.org/contribute).

Any contributions to any of our projects should follow [our code of conduct](https://openflexure.org/conduct).

If you are interested in contributing to the OpenFlexure project, and do not know where to start, we recommend you visit [our forum](https://openflexure.discourse.group/).

**The rest of this page details contribution guidelines that are specific to this repository.**

This page generally focusses on the back-end of the server, not the front-end webapp. For more details on the webapp development see the [webapp's README](webapp).

## Installation

For installation, see the [home page](README).

### Core packages and concepts

The OpenFlexure Microscope server is built in the [LabThings-FastAPI framework](https://labthings-fastapi.readthedocs.io/en/latest/). This provides a convenient way to expose laboratory hardware over HTTP via a [FastAPI server](https://fastapi.tiangolo.com/).

The core elements of the server code are `Things`. A `Thing` creates a [W3C Web of Things](https://www.w3.org/TR/wot-architecture) compliant HTTP interface for hardware interactions and other server functionality.

The project also makes use of the following libraries:

* [Pydantic](https://docs.pydantic.dev/latest/) - For serialising/deserialising data being saved to disk or sent over HTTP.
* [OpenFlexure Stitching](https://gitlab.com/openflexure/openflexure-stitching) - Our own library for creating composite microscope images from a scan.
* [OpenCV](https://opencv.org/) and [Picamera2](https://github.com/raspberrypi/picamera2/) - For controlling cameras, and simulating cameras for tests.
* [Pillow](https://pillow.readthedocs.io) - For image manipulation and saving.
* [NumPy](https://numpy.org/) and [SciPy](https://scipy.org/) - For mathematical calculations.

*The list above is not exhaustive!*

## Coding guidelines

We have separate coding guidelines for [the Python server](python) and for the [Vue webapp](webapp).


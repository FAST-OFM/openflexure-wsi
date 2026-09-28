<p align="center">
  <a href="https://github.com/FAST-OFM">
    <img src="https://github.com/FAST-OFM.png?size=200" alt="Fast OFM logo" width="132">
  </a>
</p>

<h1 align="center">OpenFlexure Microscope Software</h1>

<p align="center">
  <strong>Fast OFM · whole-slide imaging integration</strong>
</p>

<p align="center">
  <a href="https://github.com/FAST-OFM/fast-ofm">Project index</a> ·
  <a href="https://github.com/FAST-OFM/openflexure-wsi">WSI integration</a> ·
  <a href="https://github.com/FAST-OFM/controller">Controller</a> ·
  <a href="https://github.com/FAST-OFM/hardware">Hardware</a>
</p>

<p align="center">
  <sub>Independent modified distribution · OpenFlexure identity and GPL-3.0 licensing retained</sub>
</p>

## Fast OFM prototype extensions

This is Alexander Fridman's independent modified distribution of the
OpenFlexure `v3` Microscope Server. It adds prototype support for a Klipper/Moonraker stage,
multi-channel illumination, red/green autofocus, sparse focus prediction,
tissue-aware scanning and pyramidal OME-BigTIFF output.

Fast OFM does not replace the OpenFlexure identity and is not an official or
endorsed OpenFlexure release. Start with [UPSTREAM.md](UPSTREAM.md),
[MODIFICATIONS.md](MODIFICATIONS.md) and [DISCLAIMER.md](DISCLAIMER.md) before
installing or operating the prototype. The exact upstream base and modified
source snapshot are recorded there with immutable commit and tree identifiers.

This repository and every Fast OFM integration file retained inside it remain
GPL-3.0-only. Independently distributed Fast OFM algorithms are not imported as
a Python library: the server communicates with the separately installed
`fast-ofm-core` executable through a versioned JSON process protocol. Final
registration/rendering is supplied by a separately replaceable LGPL worker.
See [LICENSING.md](LICENSING.md) and [PACKAGE_LAYOUT.md](PACKAGE_LAYOUT.md).

Release-candidate backend validation for a Raspberry Pi-compatible ARM64
environment is documented in [`validation/README.md`](validation/README.md).
The Python domain boundaries and current import paths are documented in
[`PACKAGE_LAYOUT.md`](PACKAGE_LAYOUT.md).

> ## This is the v3 development branch
> We are no longer actively developing v2, but v3 is not yet released.
> v3 is not yet stable enough for wider public release. Development snapshots are released from time to time.
> If you want to use a development snapshot before we do a wider alpha release the best thing to do is get in contact on the [forum](https://openflexure.discourse.group/).
 If you want to look at the code for v2 you should look at the [master branch](https://gitlab.com/openflexure/openflexure-microscope-server/-/tree/master).


The "server" is the main component of the OpenFlexure Microscope's software.  It is responsible for controlling microscope hardware, data management, and allowing it to be controlled locally and over a network.
This repository includes the graphical interface, which is implemented as a web application served from the root of the Python web server. The simplest way to use it is via OpenFlexure eV, which should find your microscope on the network, and display the interface. The microscope's interface can also be accessed at `http://microscope.local:5000/` in a web browser, assuming the hostname of your microscope is `microscope`.

This software runs on [LabThings-FastAPI](https://github.com/labthings/labthings-fastapi/), which creates an HTTP server using FastAPI (which in turn relies on Starlette and pydantic).

## Getting started

A general user-guide on setting up your microscope can be found [**here on our website**](https://www.openflexure.org/projects/microscope/).
This includes basic installation instructions suitable for most users.
The simplest way to set up a microscope is to download the pre-built Raspberry Pi SD card image, which has this server already installed, along with all of its dependencies.
There are instructions on how to [use the microscope](https://openflexure.org/projects/microscope/control) once you have installed the software, either from OpenFlexure Connect, or through a web browser.
The web server starts on port 5000 by default, and the microscope SD image uses the hostname "microscope" so you can usually access the web interface at <http://microscope.local:5000/>.

A user guide and developer documentation **for v2 of the server** can be found on [**ReadTheDocs**](https://openflexure-microscope-software.readthedocs.io/), including some installation notes, a link to the HTTP API reference, and guidance for developing extensions.
More information is also available in the [handbook](https://gitlab.com/openflexure/microscope-handbook/), and in the "development instructions" below.

## Running directly

The Python package provides a command `ofm-microscope-server` that runs the server. This is what is used by `systemd` to run the service. You will need to provide some command-line arguments, see the output of `--help` for an up to date list. See `/etc/systemd/system/openflexure-microscope-server.service` for the command line used to run the server by default on the  Raspberry Pi. In general, you are likely to want to specify a configuration file with `-c`, a host (`--host 0.0.0.0` to serve on all addresses) and a port (`--port 5000`). The `--fallback` option will allow the server to start *even if the hardware specified in the configuration file can't load*. This serves an error page, rather than have the server fail. In the future, it should redirect users to a way to fix their configuration. The `--debug` option spins up both the OpenFlexure Microscope server and the LabThings server in `DEBUG` mode, providing more verbose logging information.

It is also possible to run the server in simulation mode. See Developer guidelines below.

## Configuration and Settings

The microscope is initially configured by a LabThings config file. This specifies two important things:

* The Python classes (and initialisation arguments) to use for each `Thing`. This sets the type of camera and stage, and enables/disables additional functionality like scanning and autofocus.
* The location of the settings folder, where each Thing can store its settings.

By default, this configuration file should be read from `/var/openflexure/settings/ofm_config.json` when the microscope is run as a service. If it is run at the command line you should specify the configuration using the `-c` command line flag. This configuration file does not change often, and usually only needs to be updated when you change the physical hardware. It may be that this file should be made read-only, particularly in microscopes deployed for e.g. medical applications.

In the future, a tool will be created for crating custom microscope configurations. Currently no custom configurations are recommended, except for development. More [information on the configuration file](https://openflexure.gitlab.io/openflexure-microscope-server/dev/index.html#config-file) can be found in the developer documentation.

The settings folder holds the state of your microscope, including the calibrations. The settings folder is, by default, `/var/openflexure/settings/` if running on a Raspberry Pi, or `./settings/` if run elsewhere. It can be changed in the configuration file. This holds every other persistent setting. Camera settings, calibration data, default capture settings, stream resolution and so on. There is one folder per `Thing`, each with their own file. The settings files can change very regularly, and if you need to reset your settings, it's usually these files that you should remove or reset. If you delete one settings file this should reset the corresponding `Thing`, you don't have to delete the whole folder.

## Development

For development please see our [developer documentation](https://openflexure.gitlab.io/openflexure-microscope-server/dev/index.html).

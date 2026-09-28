"""A package responsible for, setup, booting, and shutting down the server."""

from __future__ import annotations

import logging
import os
from argparse import Namespace
from copy import copy
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uvicorn.main import Server

import labthings_fastapi as lt
from labthings_fastapi.server import fallback
from labthings_fastapi.server.config_model import ThingServerConfig

from openflexure_microscope_server.things.camera import BaseCamera
from openflexure_microscope_server.utilities import load_patched_config

from ..logging import (
    OFM_HANDLER,
    configure_logging,
    retrieve_log,
    retrieve_log_from_file,
)
from .legacy_api import add_v2_endpoints
from .serve_static_files import add_static_files

LOGGER = logging.getLogger(__name__)
DEVELOPER_MODE = os.getenv("OFM_SERVER_DEV_MODE", "false").lower() == "true"
_TEMPLATE_PATH = Path(__file__).with_name("fallback.html.jinja")


class OFMApplicationData(BaseModel):
    """Application data for the OpenFlexure Microscope."""

    log_folder: str
    """The directory to save the logs in."""
    data_folder: str
    """The directory for Things to save data in."""


def set_shutdown_function(shutdown_function: Callable[[], None]) -> None:
    """Ensure a function is called before the shutdown.

    This monkey patches the Uvicorn Server's handle_exit. This is needed because
    the uvicorn ``lifecycle`` events and FastAPI ``shutdown`` events only fire once
    background tasks have completed.

    Without this the system exits cleanly only if no client is receiving a
    StreamingResponse. This patch is used to stop the async generators that
    send streaming responses.

    :param shutdown_function: A callable with no arguments or outputs. This
        should stop any async generators that may be sending to streaming responses.
    """
    original_handler = Server.handle_exit

    @wraps(Server.handle_exit)
    def handle_exit(*args: Any, **kwargs: Any) -> None:
        shutdown_function()
        original_handler(*args, **kwargs)

    # Ignore the MyPy doesn't want us monkey patching. We have to unless the
    # FastAPI lifecycle is fixed.
    Server.handle_exit = handle_exit  # type: ignore[method-assign]


def customise_server(
    server: lt.ThingServer, application_config: OFMApplicationData
) -> None:
    """Customise the server with additional endpoints, debug mode etc."""
    if DEVELOPER_MODE:
        # Allow CORS in developer mode for easier testing with the webapp
        server.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    add_v2_endpoints(server)
    add_static_files(server, application_config.data_folder)

    # Add an endpoint to get the logs - (directly calling the FastAPI decorator)
    server.app.get(str(server.api_prefix.rstrip("/")) + "/log/")(retrieve_log)
    server.app.get(str(server.api_prefix.rstrip("/")) + "/logfile/")(
        retrieve_log_from_file
    )


def serve_from_cli(argv: Optional[list[str]] = None) -> None:
    """Start the server from the command line."""
    args = lt.cli.parse_args(argv)

    log_config = copy(uvicorn.config.LOGGING_CONFIG)
    log_config["loggers"]["uvicorn"]["propagate"] = True
    log_config["loggers"]["uvicorn.access"]["propagate"] = True

    # Create server and lt_config vars before trying to configure so they are defined
    # if fallback is needed before they are set.
    lt_config = None
    server = None
    try:
        lt_config = _full_config_from_args(args)
        debug = bool(args.debug)
        # Validate our application data
        if lt_config.application_config is None:
            raise ValueError("No application configuration was supplied.")
        application_config = OFMApplicationData(**lt_config.application_config)
        configure_logging(application_config.log_folder, debug)

        server = lt.ThingServer.from_config(lt_config, debug)
        customise_server(server, application_config)
        OFM_HANDLER.set_action_manager(server.action_manager)

        def shutdown_call() -> None:
            try:
                camera_thing = server.things["camera"]
                if not isinstance(camera_thing, BaseCamera):
                    raise RuntimeError("Camera thing is not a BaseCamera")
                camera_thing.kill_mjpeg_streams()
            except BaseException as e:
                # Catch anything and log as it is essential that this
                # function cannot raise an unhandled exception or Uvicorn
                # will never get a shutdown signal.
                LOGGER.error(e, exc_info=True)

        # Monkey patch uvicorn's exit handling to stop the MJPEG Streams
        # before waiting for background tasks to complete.
        set_shutdown_function(shutdown_call)

        uvicorn.run(
            server.app,
            host=args.host,
            port=args.port,
            log_config=log_config,
            timeout_graceful_shutdown=2,
        )

    except BaseException as e:
        if args.fallback:
            # Allow printing to the terminal for fallback errors so they are not
            # presented in the fallback logs.
            print(f"Error: {e}")  # noqa: T201
            print("Starting fallback server.")  # noqa: T201
            try:
                log_history = OFM_HANDLER.get_formatted_log_history
            except BaseException:
                # If log history fails for any reason carry on.
                log_history = None

            app = fallback.app
            app.set_template_str(_TEMPLATE_PATH.read_text(encoding="utf-8"))
            app.set_context(
                fallback.FallbackContext(
                    server=server, config=lt_config, error=e, log_history=log_history
                )
            )
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                log_config=log_config,
            )
        else:
            raise e


def _full_config_from_args(args: Namespace) -> ThingServerConfig:
    """Load configuration from LabThings args allowing patching.

    This returns the labthings ThingServerConfig model.

    This provides similar functionarlity to lt.cli.config_from_args except allows the
    configuration file to specify a base config, and optionally patches.
    """
    if not args.config:
        raise RuntimeError(
            "OpenFlexure Microscope Server must have a configuration file specified."
        )

    patched_config = load_patched_config(args.config)
    if "enable_global_lock" not in patched_config:
        patched_config["enable_global_lock"] = True
    if patched_config["enable_global_lock"] is not True:
        raise ValueError(
            "OpenFlexure Microscope Server requires `enable_global_lock` to be `true`, "
            "and it was set to another value in the configuration file."
        )
    return ThingServerConfig(**patched_config)

"""Test utilities for interacting with the labthings Server."""

import tempfile
import time
from types import TracebackType
from typing import Any, Mapping, Optional, Self, TypeVar

import requests
from fastapi.testclient import TestClient

import labthings_fastapi as lt

ThingSubclass = TypeVar("ThingSubclass", bound=lt.Thing)

ACTION_RUNNING_KEYWORDS = ["idle", "pending", "running"]


class LabThingsTestEnv:
    """A Labthings Server running in a FastAPI Test Client.

    This can be used to create ThingClients for testing, to to enable direct access to
    the server, or things on the server. It also provides high level functions to
    start actions without blocking the test thread.

    Use this as a context manager:

    .. code-block:: python

        with LabThingsTestEnv(things={"counter", CountingThing}) as env:
            ...
    """

    def __init__(
        self,
        things: Mapping[str, lt.Thing | str],
        settings_folder: Optional[str] = None,
        application_config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Initialise the test environment.

        The server and FastAPI TestClient are server are not created until this is used
        as a context manager:

        :param things: The thing configuration dictionary used to initialise the server.
        :param settings_folder: The settings folder to use. If None a tempdir is created
            for this.
        :param application_config: The application configuration dictionary. If None is
            supplied then "data_folder" is set to a temporary directory.
        """
        self._server: Optional[lt.ThingServer]
        self._test_client: Optional[TestClient]
        self._things_config = things
        self._settings_folder = settings_folder
        self._application_config = application_config
        self._settings_tmp_dir: Optional[tempfile.TemporaryDirectory] = None
        self._data_tmp_dir: Optional[tempfile.TemporaryDirectory] = None

    def __enter__(self) -> Self:
        """Create server and run it in the TestClient."""
        # Any OFMThings require an application config for the data_folder
        if self._application_config is None:
            self._data_tmp_dir = tempfile.TemporaryDirectory()
            self._application_config = {"data_folder": self._data_tmp_dir.name}
        if self._settings_folder is None:
            self._settings_tmp_dir = tempfile.TemporaryDirectory()
            self._settings_folder = self._settings_tmp_dir.name
        self._server = lt.ThingServer(
            things=self._things_config,
            settings_folder=self._settings_folder,
            application_config=self._application_config,
        )
        self._test_client = TestClient(self._server.app)
        self._test_client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the TestClient and cleanup."""
        self._test_client.__exit__(exc_type, exc_value, traceback)
        if self._settings_tmp_dir is not None:
            self._settings_tmp_dir.cleanup()
        if self._data_tmp_dir is not None:
            self._data_tmp_dir.cleanup()

    @property
    def server(self) -> lt.ThingServer:
        """The LabThings Server."""
        if self._server is None:
            raise RuntimeError("No server was started.")
        return self._server

    @property
    def client(self) -> TestClient:
        """The FastAPI TestClient running the server."""
        if self._test_client is None:
            raise RuntimeError("No server was started.")
        return self._test_client

    def check_thing_exists(self, thing_name: str):
        """Raise a ValueError if no thing of with a matching name exists."""
        if thing_name not in self.server.things:
            raise ValueError(f"No Thing named {thing_name}")

    def get_thing_by_name(self, thing_name: str) -> lt.Thing:
        """Get a Thing from the server by name.

        :param thing_name: The name of the thing to on the server.

        :return: The Thing with the specified name.
        """
        self.check_thing_exists(thing_name)
        return self.server.things[thing_name]

    def get_thing_by_type(self, thing_class: type[ThingSubclass]) -> ThingSubclass:
        """Get a thing by type.

        :param thing_class: The subclass of thing to match.

        :return: The Thing that matches the subclass.

        :raises RuntimeError: If there are multiple things of the same type, or no
            matching thing. If there are multiple things of this type use
            ``get_thing_by_name`` or ``get_all_things_by_type``.
        """
        matching = self.get_all_things_by_type(thing_class)
        n_things = len(matching)
        if n_things == 0:
            raise RuntimeError(f"No Thing of type {thing_class} on this server.")
        if n_things > 1:
            raise RuntimeError(
                f"Cannot get Thing by type as there are {n_things} of type "
                f"{thing_class} on this server."
            )
        return next(iter(matching.values()))

    def get_all_things_by_type(
        self, thing_class: type[ThingSubclass]
    ) -> Mapping[str, ThingSubclass]:
        """Get a dictionary of all things by matching a type.

        :param thing_class: The subclass of thing to match.

        :return: A dictionary of Things that match the subclass. If none match this
            will be and empty dictionary.
        """
        matching = {}
        for thing_name, thing in self.server.things.items():
            if isinstance(thing, thing_class):
                matching[thing_name] = thing
        return matching

    def get_thing_client(self, thing_name: str) -> lt.ThingClient:
        """Get a ThingClient for a Thing by name.

        :param thing_name: The name of the thing to on the server.

        :return: A LabThings ThingClient for the Thing with the specified name.
        """
        self.check_thing_exists(thing_name)
        thing = self.server.things[thing_name]
        return lt.ThingClient.from_url(thing.path, self.client)

    def start_action(
        self,
        thing_name: str,
        action_name: str,
        action_kwargs: Optional[Mapping[str, Any]] = None,
    ) -> requests.Response:
        """Start an action and return the server response.

        For most purposes the best way to run an action is to use ``get_thing_client``
        to create a ThingClient. At this point any actions can be run with a similar
        Python API to calling directly. However, using ThingClient blocks the test
        thread.

        This function provides an alternative way to start actions without blocking the
        test thread. It will return the HTTP response, this response can be used to
        poll or cancel the action. Use this method if you:

        * Want to test cancelling an action.
        * Want to inspect the actions logs exactly as they would come to a web client
        * Direcltly interact with the HTTP API

        :param thing_name: The name of the Thing on the server.
        :param action_name: The name of the action to start.
        :action_kwargs: The keyword inputs to the action.
        :return: A Response object with the HTTP response.
        """
        self.check_thing_exists(thing_name)
        url = f"/{thing_name}/{action_name}"
        json_payload = {} if action_kwargs is None else action_kwargs
        response = self.client.post(url, json=json_payload)
        response.raise_for_status()
        return response

    def poll_action(
        self, response: requests.Response, interval: float = 0.01
    ) -> Mapping[str, Any]:
        """Poll an action until it completes and return the final response data.

        :param response: The response from starting this action with ``start_action``.
        """
        invocation_data = response.json()

        if "status" not in invocation_data:
            raise ValueError(f"Response json has no status: {invocation_data}")
        first_run = True
        while invocation_data["status"] in ACTION_RUNNING_KEYWORDS:
            if first_run:
                first_run = False
            else:
                time.sleep(interval)
            response = self.client.get(_invocation_href(invocation_data))
            response.raise_for_status()
            invocation_data = response.json()
        return invocation_data

    def cancel_action(self, response: requests.Response) -> None:
        """Cancel an ongoing action.

        :param response: The response from starting this action with ``start_action``.
        """
        invocation_data = response.json()
        response = self.client.delete(_invocation_href(invocation_data))
        response.raise_for_status()


def _get_link(obj: Mapping[str, Any], rel: str) -> Mapping[str, Any]:
    """Retrieve a link from an object's `links` list, by its `rel` attribute."""
    return next(link for link in obj["links"] if link["rel"] == rel)


def _invocation_href(invocation_data: Mapping[str, Any]) -> str:
    """Get the invocation href from the invocation response data."""
    return _get_link(invocation_data, "self")["href"]

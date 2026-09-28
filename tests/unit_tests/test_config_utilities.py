"""Test the configuration handling functions in utilities."""

import json
import os
from json import JSONDecodeError

import pytest

from openflexure_microscope_server import utilities


@pytest.mark.parametrize(
    ("target", "patch", "outcome", "err_if_enforce"),
    [
        ({"a": "b"}, {"a": "c"}, {"a": "c"}, False),
        ({"a": "b"}, {"b": "c"}, {"a": "b", "b": "c"}, False),
        ({"a": "b"}, {"a": None}, {}, False),
        ({"a": "b", "b": "c"}, {"a": None}, {"b": "c"}, False),
        ({"a": ["b"]}, {"a": "c"}, {"a": "c"}, False),
        ({"a": "c"}, {"a": ["b"]}, {"a": ["b"]}, False),
        (
            {"a": {"b": "c"}},
            {"a": {"b": "d", "c": None}},
            {"a": {"b": "d"}},
            False,
        ),
        ({"a": [{"b": "c"}]}, {"a": [1]}, {"a": [1]}, False),
        (["a", "b"], ["c", "d"], ["c", "d"], True),
        ({"a": "b"}, ["c"], ["c"], True),
        ({"a": "foo"}, None, None, True),
        ({"a": "foo"}, "bar", "bar", True),
        ({"e": None}, {"a": 1}, {"e": None, "a": 1}, False),
        ([1, 2], {"a": "b", "c": None}, {"a": "b"}, True),
        ({}, {"a": {"bb": {"ccc": None}}}, {"a": {"bb": {}}}, False),
    ],
)
def test_merge_patch(target, patch, outcome, err_if_enforce):
    """Check that merge_patch returns the expected outcome for each target and patch.

    These test cases are specified in Appendix A of IETF RFC 7396.
    """
    assert utilities.merge_patch(target, patch) == outcome

    # If expected to error when enforce_dict is True then check:
    if err_if_enforce:
        with pytest.raises(ValueError, match="Both target and patch"):
            utilities.merge_patch(target, patch, enforce_dict=True)
    else:
        # Else should run without error with same expected value
        assert utilities.merge_patch(target, patch, enforce_dict=True) == outcome


@pytest.mark.parametrize(
    ("base_conf", "resolves_to"),
    [
        ("/var/base.json", os.path.normpath("/var/base.json")),
        ("base.json", os.path.normpath("/var/openflexure/settings/base.json")),
        ("./base.json", os.path.normpath("/var/openflexure/settings/base.json")),
        ("../base.json", os.path.normpath("/var/openflexure/base.json")),
        (
            "$OFM_LIB/base.json",
            os.path.normpath(
                "/var/openflexure/application/openflexure-microscope-server/base.json"
            ),
        ),
    ],
)
def test_resolve_path_from_dir(base_conf, resolves_to, mocker):
    """Test that resolve path handles, absolute and relative paths and expands env vars."""
    mocker.patch.dict(
        os.environ,
        {"OFM_LIB": "/var/openflexure/application/openflexure-microscope-server"},
    )
    conf_dir = "/var/openflexure/settings"
    resolved_path = utilities.resolve_path_from_dir(base_conf, directory=conf_dir)
    assert resolved_path == resolves_to


def test_patching_config(tmpdir):
    """Test each situation in load_patched_config."""
    conf_file = os.path.join(tmpdir, "ofm_config.json")
    base_conf_file = os.path.join(tmpdir, "ofm_base_config.json")

    simple_config = {"things": {"thing1": "python.class"}}
    config_patch = {"things": {"thing2": "python.class2"}}
    patched_config = {"things": {"thing1": "python.class", "thing2": "python.class2"}}
    broken_json = "{{invalid{Json"

    # Error if config file doesn't exist
    with pytest.raises(IOError, match="Couldn't load configuration"):
        utilities.load_patched_config(conf_file)

    # Error if config file contains bad json
    with open(conf_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(broken_json)
    with pytest.raises(JSONDecodeError, match="Invalid JSON in configuration"):
        utilities.load_patched_config(conf_file)

    # If base_config_file is not set, just return the configuration
    with open(conf_file, "w", encoding="utf-8") as file_obj:
        json.dump(simple_config, file_obj)
    assert utilities.load_patched_config(conf_file) == simple_config

    # Set the "base_config_file" instead, error as there is no base config file
    config = {"base_config_file": "./ofm_base_config.json"}
    with open(conf_file, "w", encoding="utf-8") as file_obj:
        json.dump(config, file_obj)
    with pytest.raises(IOError, match="Couldn't load base configuration"):
        utilities.load_patched_config(conf_file)

    # Error if base config file contains bad json
    with open(base_conf_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(broken_json)
    with pytest.raises(JSONDecodeError, match="Invalid JSON in base configuration"):
        utilities.load_patched_config(conf_file)

    # Loads the simple configuration from the base config file.
    with open(base_conf_file, "w", encoding="utf-8") as file_obj:
        json.dump(simple_config, file_obj)
    assert utilities.load_patched_config(conf_file) == simple_config

    # Set the outer config to have a patch.
    # It now reads the simple_config from the base condif file and patches it
    config["patch"] = config_patch
    with open(conf_file, "w", encoding="utf-8") as file_obj:
        json.dump(config, file_obj)
    assert utilities.load_patched_config(conf_file) == patched_config

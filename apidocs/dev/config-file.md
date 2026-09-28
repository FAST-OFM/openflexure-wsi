# The Configuration File

By default, this configuration file is read from `/var/openflexure/settings/ofm_config.json` when the microscope is run as a service. If it is run at the command line, you should specify the configuration file path using the `-c` command line flag.

The default configuration file links to the configuration file in the repository:

```json
{
    "base_config_file": "/var/openflexure/application/openflexure-microscope-server/ofm_config_full.json"
}
```

## Understanding and modifying the base configuration file

This section describes how to change the microscope configuration for development. For example, when making a Merge Request to add a new Thing to the standard microscope configuration. **Do not do this for customising a single microscope.** For customiston please refeer to the section on [customising the configuration](#section-customise-config).

The specification for the configuration file is set by [LabThings FastAPI](https://labthings-fastapi.readthedocs.io/). The file is a JSON file. As such, it cannot contain comments.

The configuration file has three keys:

* `things` - A dictionary (Object) containing the [`Thing`s](https://labthings-fastapi.readthedocs.io/en/latest/using_things.html) that should be loaded. All instrument control and server interaction is via `Thing`s.
* `settings_folder` - The directory that LabThings will save settings for each `Thing`.
* `application_config` - A space for the OpenFlexure Microscope Server to list its custom configuration. The OpenFlexure Microscope Server has the following keys:
  * `log_folder` - Where the log files are stored.
  * `data_folder` - Where data is stored.

The `things` dictionary can specify a `Thing` to be loaded into the server in two different ways. If there is no custom configuration for the `Thing` then:

* The key is a **name** for the `Thing`, which defines the URL of its endpoints.
* The value is the Python object reference for the `Thing`.

For example:

```json
"autofocus": "openflexure_microscope_server.things.focus.autofocus:AutofocusThing",
```

will add an autofocus Thing to the server. The Thing is located at `openflexure_microscope_server.things.focus.autofocus:AutofocusThing`, (allowing multiple Things per Python file), and any endpoints (such as actions and properties) in that Thing will have the URL `base_url:port/autofocus/endpoint_name`

If further information needs to be supplied then the value should be a dictionary (Object) with the following keys:

* `class` - *(Required)* The Python object reference for the `Thing`.
* `args` - *(Optional)* The positional arguments to be passed to the `Thing` when initialised.
* `kwargs` - *(Optional)* The keyword arguments to be passed to the `Thing` when initialised.
* `thing_slots` - *(Optional)* Information for how the [thing slots](https://labthings-fastapi.readthedocs.io/en/latest/thing_slots.html) of the `Thing` are populated.

For example:

```json
"camera": {
    "class": "openflexure_microscope_server.things.camera.picamera:StreamingPiCamera2",
    "kwargs": {
        "camera_board": "picamera_v2"
    }
}
```

## Customising the configuration :id=section-customise-config


To have a custom configuration for a specific microscope, we recommend editing `ofm_config.json` in the settings folder. **Avoid editing the base configuration in the repository.** This will keep your customisations that should not be pushed separate from changes to the base file.

The customisations are performed using [**JSON merge patch** (IETF RFC 7396)](https://datatracker.ietf.org/doc/html/rfc7396) syntax. The patch should specify only the keys that are to change. The RFC documentation provides a number of examples.

> **Note**
> If a key is set to `null` that key is deleted entirely. There is a [LabThings FastAPI issue](https://github.com/labthings/labthings-fastapi/issues/190) discussing how to handle the rare cases where a value may need to be patched to have the value `None` in Python.

For our configuration file, an extra key `patch` is added that describes how to patch the base configuration file.

### Configuration for the Raspberry Pi HQ Camera

> **Note**
> HQ camera support is currently experimental in v3. While it can be used, it is not as thoroughly tested or supported as the standard v2 camera module.

You can configure a microscope for the Raspberry Pi HQ Camera by specifying the `camera_board` keyword argument for the `camera` `Thing`. The value needs to change from `picamera_v2` to `picamera_hq`. This will load the HQ camera tuning file and configure the pipeline accordingly.

As the only key that needs changing is `things.camera.kwargs.camera_board` the patch is:

```json
{
    "base_config_file": "/var/openflexure/application/openflexure-microscope-server/ofm_config_full.json",
    "patch": {
        "things": {
            "camera": {
                "kwargs": {
                    "camera_board": "picamera_hq"
                }
            }
        }
    }
}
```

This will change the camera section to:

```json
"camera": {
    "class": "openflexure_microscope_server.things.camera.picamera:StreamingPiCamera2",
    "kwargs": {
        "camera_board": "picamera_hq"
    }
}
```

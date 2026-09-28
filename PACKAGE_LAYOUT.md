# Python package layout

The server is organised by responsibility. The package boundaries are intended to
make each FAST-OFM subsystem understandable and reusable without searching a flat
module directory.

| Package | Responsibility |
| --- | --- |
| `acquisition` | JPEG/raw capture geometry and measurement previews |
| `focus` | Focus lifecycle, sparse scheduling, process contracts, and Z backlash |
| `focus.rg` | Red-green settings, evidence and wire contracts; no numerical focus implementation |
| `scanning` | Scan planners and on-disk scan lifecycle |
| `stitching` | GPL process orchestration, preview stitching and final-output validation |
| `integrations.fast_ofm_core` | Versioned JSON process client and immutable artifact writers |
| `things.focus` | Focus-related LabThings API adapters |
| `things.scanning` | Scan-related LabThings API adapters |
| `things.stage` | Stage drivers, mapping, motion checks, and measurement |
| `things.camera` | Camera drivers and camera-specific helpers |

Cross-cutting server infrastructure remains at the package root: configuration
contracts, gallery storage, logging, UI descriptions, and general utilities.

Final registration, accelerated mosaic rendering and OME-BigTIFF/DZI writing
are intentionally absent from this GPL package. The server submits an immutable
tile manifest to the separately installed `fast-ofm-core` executable, which in
turn invokes the replaceable LGPL `fast-ofm-stitch-openflexure` worker. No
noncommercial implementation is imported into the OpenFlexure Python process.
The same boundary is used for flat-field operations, WHITE tissue-field
preparation, R/G registration, simultaneous RAW spectral unmixing, calibration,
focus-surface prediction and exact route planning.

## Import migration

This prototype release moves internal Python module paths. HTTP routes and Thing
names are unchanged. Configurations should use the module paths shipped in
`ofm_config_full.json` or `ofm_config_simulation.json`. Python integrations should
import from the domain package that owns the implementation; for example:

```python
from openflexure_microscope_server.focus.focus_scan import FocusScanSettings
from openflexure_microscope_server.focus.rg.rg_focus_model import RGFocusModelProfile
from openflexure_microscope_server.stitching.stitching import StitchingSettings
from openflexure_microscope_server.things.scanning.smart_scan import SmartScanThing
```

The former flat module paths are intentionally not retained as aliases. This keeps
the package boundary explicit and prevents new code from depending on the legacy
layout.

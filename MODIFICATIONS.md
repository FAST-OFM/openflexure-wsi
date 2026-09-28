# Fast OFM modifications

This file groups the changes made to the OpenFlexure Microscope Server for the
Fast OFM research prototype. Stable feature IDs
correspond to the project-level feature index in `FAST-OFM/fast-ofm`.

## Motion and controller integration

- `STAGE-001`: Moonraker/Klipper XYZ stage backend.
- `MOTION-001`: bounded motion policy and completion/readback handling.
- `FOCUS-002`: Z approach, preload/backlash policy and calibration support.

## Illumination and capture

- `LIGHT-001`: independently controlled WHITE, RED and GREEN illumination.
- `CAPTURE-001`: prepared JPEG/RAW measurement capture and preview status.

The external Arduino brightness controller and MKS timing-gate implementation
are maintained in the companion `FAST-OFM/controller` and
`FAST-OFM/hardware` repositories.

## Red/green focus and focus prediction

- `AF-001` through `AF-006`: hardware sequencing, persisted contracts and
  telemetry for RG measurement/calibration, simultaneous RAW acquisition,
  tissue-patch recovery, bounded correction and WHITE fallback. Numerical
  flat-field, tissue-mask, NMI, spectral-unmixing and focus-model operations
  execute in the separately installed Fast OFM Core process.
- `FOCUS-001`: sparse focus anchors and local focus-surface prediction.
- `SCAN-002`: focus-strategy selection and telemetry during scanning.

## Scanning, UI and WSI output

- `SCAN-001`: bounded tissue-aware snake/spiral scan workflows.
- `UI-001`: operator controls and calibration/status surfaces.
- `OUTPUT-001`: pyramidal OME-BigTIFF output.
- `STITCH-001`: bounded FFT/cache and rendering acceleration.
- `STITCH-002`: disconnected-region positioning by stage coordinates and local
  correlation.

The GPL server now retains hardware/process orchestration, strict wire
contracts, WHITE fallback, preview stitching and final-output validation.
Numerical focus and job-policy operations live in Fast OFM Core; final
registration/rendering lives in the separately installed LGPL worker. The
boundary uses immutable SHA-256 artifacts and versioned JSON requests, and
cancellation terminates the complete subprocess group.

## Identity and distribution changes

- Restored the OpenFlexure name, visual identity, About page and upstream help
  links after an internal prototype build had replaced them.
- Added explicit Fast OFM modified-work, provenance and non-endorsement notices.
- Removed private operational records and machine-specific settings from the
  clean release snapshot.
- Consolidated prototype-only configuration, telemetry and internal helper
  identifiers under the `fast_ofm` namespace. This clean snapshot does not
  provide compatibility aliases for unreleased private namespaces; migrate any
  locally retained settings explicitly instead of copying a private settings
  directory into the release.

## Clean-release preparation

- Added private-release CI, issue templates, security/provenance records and a
  digest-pinned ARM64 validation target.
- Made system-state tests independent of the host UUID and executable name,
  and allowed the regex property test to run under slow architecture emulation
  without changing its generated examples or assertions.
- Made temporary-Git tests independent of workstation identity and replaced a
  fixed 100 ms action-timestamp assumption with a call-boundary assertion.
- Removed simulated-camera noise from the repeated autofocus integration check
  so architecture emulation tests the autofocus contract deterministically.

These release-preparation changes were applied after exporting the immutable
engineering input recorded in `UPSTREAM.md`; the clean release commit is the
authoritative distributed tree.

## Evidence boundary

The current accepted prototype is host-orchestrated and stop-and-shoot. MCU
frame synchronization and continuous global-shutter scanning are not claimed
as working release capabilities. See `DISCLAIMER.md` and the canonical project
`STATUS.md` before interpreting any feature as production-ready.

# Contributing

This repository is a modified OpenFlexure Microscope Server distribution. Read
`UPSTREAM.md`, `MODIFICATIONS.md`, and the upstream developer guidance in
`apidocs/dev/contributing.md` before changing it.

Fast OFM additions use the stable feature IDs documented in the
[`FAST-OFM/fast-ofm` feature index](https://github.com/FAST-OFM/fast-ofm/blob/v0.3.0-prototype.1/FEATURE_INDEX.md).
Keep changes focused so motion, illumination, RG focus, and stitching can be
reviewed independently. New hardware behaviour must fail closed, remain
disabled until explicitly configured, and include tests. Preserve OpenFlexure
identity, copyright notices, and GPL-3.0 terms.

All contributions to this combined distribution must be compatible with
GPL-3.0-only. Do not copy PolyForm-Noncommercial or CC-BY-NC-SA material from
companion repositories into this GPL-covered program.

Do not commit real credentials, private deployment files, patient/specimen
identity, or machine calibration as a universal default. Run the Python and
web validation described in `validation/README.md` and include exact results in
the pull request.

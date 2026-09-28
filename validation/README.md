# Raspberry Pi-compatible validation

This validation target runs the GPL integration suite in a clean Debian 13
(`trixie`) `linux/arm64` container. The optional `_rg_nmi` C++ extension and
all numerical R/G implementations now belong to the separately installed
`fast-ofm-core` package; this GPL tree intentionally contains neither.

The target runs the complete unit and integration suites. Tests under
`tests/hardware_specific_tests` are deliberately excluded because they require
a physical Raspberry Pi camera stack and are not meaningful in this
hardware-independent container.

The ARM64 target overrides the repository's coverage-reporting `addopts`.
Coverage is collected by ordinary CI; disabling it here avoids spending most
of the emulated runtime instrumenting Python and rendering HTML reports without
changing which tests or assertions execute.
The target passes `--import-mode=importlib` explicitly because that functional
option is also present in the repository's normal `addopts`.

The Debian manifest digest is pinned in the Dockerfile so a release candidate
does not silently move to a newer base image.

The container is intentionally hardware-independent: camera, GPIO, serial and
Moonraker interactions remain mocked. A real Raspberry Pi smoke test is still
required before moving the prototype beyond release-candidate status.

## Recorded final staging evidence

On 2026-09-28 the current extracted GPL staging tree was tested in a clean
CPython 3.13 container on the private Linux x86_64 server, limited to three CPU
cores and 4 GiB RAM. The standalone adapter selection, with the separately
licensed core deliberately absent, reported `1398 passed, 4 skipped, 3
xfailed` in 184.24 seconds. Optional cross-package modules are excluded as one
explicit suite in that mode instead of relying on partially imported test
helpers. With `fast-ofm-core` installed separately, the complete adapter and
process-integration selection reported `1705 passed, 3 xfailed` in 277.17
seconds. Core numerical parity is exercised in the companion repository's
clean x86_64 and ARM64 gates, both at `452 passed`.

A separate two-field replay exercised the complete process chain:
OpenFlexure `FinalStitcher` → `fast-ofm-core run-request` → the independently
installed LGPL stitching worker. It produced an 81,235,878-byte OME-BigTIFF
and a complete DZI pyramid in 10.11 seconds wall time, with 380,576 KiB peak
RSS. The retained 91-field replay and pixel-parity evidence are recorded in the
companion core and worker repositories.

The packaging gate then built all three Linux wheels and installed them into a
fresh CPython 3.11 environment with no editable source paths. The packaged
process chain completed the same two-field replay in 5.89 seconds wall time
with 365,392 KiB peak RSS and no swap. An older OME-TIFF deliberately remained
in the scan directory; manifest-only input isolation prevented it from being
rediscovered as a source frame. The new 4068×5624 level 0 matched the retained
reference exactly; the 64,782,770-byte file used one base IFD plus four SubIFDs
because the fresh environment supplied libvips 8.10+. A later QuPath 0.5.1
check showed that the earlier libvips 8.9 five-page form is exposed as one
level, so release OME output now requires libvips 8.10+ and refuses older hosts
before rendering. DZI-only output remains available on those hosts.

The installed GPL client was also exercised with no core executable and with a
mock protocol-2.0 executable. It returned retryable `CORE_UNAVAILABLE` for the
first deployment and failed closed with `CORE_PROTOCOL_ERROR` for the second.

Run from the repository root:

```sh
docker buildx build \
  --platform linux/arm64 \
  --load \
  --tag fast-ofm/openflexure-wsi-validation:local \
  --file validation/Dockerfile.pi-arm64 \
  .

docker run --rm \
  --platform linux/arm64 \
  --cpus 3 \
  --memory 4g \
  fast-ofm/openflexure-wsi-validation:local
```

For a release record, save the resolved base-image digest, host architecture,
Docker version, full test summary and the resulting image ID.

On an x86_64 host using QEMU, the release gate may be split by sorted test file
into three isolated containers. Limit each container to one CPU and 1,300 MiB;
the aggregate remains below three CPUs and 4 GiB. Sharding changes scheduling,
not the selected test nodes, and separate containers preserve file-level test
isolation.

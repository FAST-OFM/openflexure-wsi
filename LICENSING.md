# Licensing boundary

This repository is a modified distribution of the OpenFlexure Microscope
Server. The combined work, including the Fast OFM modifications distributed
inside it, is licensed under `GPL-3.0-only`. The complete unmodified license
text is in `LICENSE`.

GPL-3.0 permits commercial use, modification and redistribution when its terms
are followed. A noncommercial restriction is not added to this repository
because it would be an incompatible additional restriction.

Original Fast OFM material that is independently distributed outside this
combined GPL work may use a different license, as documented in the companion
repositories. That separate treatment does not relicense this repository or
remove any GPL rights.

The optional `fast-ofm-core` program is an independently installed process. It
is not imported or linked into this Python package; interoperability uses
Apache-2.0 protocol schemas, JSON values and verified file artifacts. The
replaceable `fast-ofm-stitch-openflexure` process contains the implementation
that depends on `openflexure-stitching` and remains LGPL-3.0-only. Users may run
this GPL server without either optional process, although the corresponding
Fast OFM capabilities will be unavailable or fall back as documented.

The immutable `v0.2.0-prototype.1` and later copies keep all rights granted
under the GPL. Future fully independent Fast OFM implementations must maintain
a clear source and runtime boundary if they use a non-GPL license.

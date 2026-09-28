# Local Material Symbols subset

Derived from Google Material Symbols, distributed by the lockfile-pinned
`material-symbols` npm package (0.44.12). Apache-2.0; see LICENSE.
Modified by subsetting to this UI's icons; glyph designs and variable axes unchanged.

The upstream outlined font is 3,962,536 bytes. On the Pi/VPN it took 14.24 seconds
to download during the reported failure; before loading, ligature names could
appear as overflowing text. The local subset is preloaded and icon boxes are
bounded. There are no Google Fonts/CDN requests.

The committed WOFF2 is sufficient for normal builds. To add an icon, update
`icons.json` and regenerate the subset from the lockfile-pinned upstream font.
Verify every required ligature still shapes into its original glyph before
committing the regenerated WOFF2. Dynamic choices (copy/check,
expand/collapse, default menu icon) are listed too.

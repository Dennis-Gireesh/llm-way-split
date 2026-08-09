# Embedded Native Components

This record complements the release SBOMs. Python-package and operating-system
SBOM scanners do not always identify libraries embedded in wheels or compiled
extensions, so an SBOM by itself is insufficient as a native-code inventory.

## Locked distributions

`uv.lock` pins the distributions and hashes used for each supported platform.
For the Alpine Linux targets used by the container image, the relevant locked
wheels are:

| Architecture | Distribution | Locked artifact SHA-256 |
| --- | --- | --- |
| `linux/arm64` | `pillow==12.3.0` `musllinux_1_2_aarch64` | `e491916b378fba47242221bb9ead245211b70d504f495d105d17b14a24b4907c` |
| `linux/amd64` | `pillow==12.3.0` `musllinux_1_2_x86_64` | `0dd2064cbc55aaec028e5fbb60fa47bb6c3e7918e07ff17935284b227a9d2df` |
| `linux/arm64` | `pypdfium2==5.12.1` `musllinux_1_2_aarch64` | `4648f0905441bcb141687ca2263bbf38a1aa056b943eef06019f91cff3e1da4a` |
| `linux/amd64` | `pypdfium2==5.12.1` `musllinux_1_2_x86_64` | `715ae16b34ea1d64884d58800155179ba700e9ea65a2f583b020666acd2bfb12` |

Release artifacts remain authoritative for the architecture actually
published. Verify their digest, SBOM, and attestation rather than assuming that
the table above identifies a locally rebuilt image.

Every tagged release also attaches `native-inventory-linux-amd64.json` and
`native-inventory-linux-arm64.json`. The release gate creates each inventory by
running PDFium rendering, Pillow native codec operations, and English Tesseract
OCR inside the exact hardened leaf image. It records bounded version and hash
evidence, including that leaf's `libpdfium.so` SHA-256; the multi-platform
manifest is not created unless both inventories exist and both native smokes
pass. Each inventory is also covered by the release `SHA256SUMS` file.

## PDFium bundled by pypdfium2

Inspection of the `linux/arm64` Alpine release-candidate image built from this
lock state reported:

| Component | Reported version/build | Evidence |
| --- | --- | --- |
| pypdfium2 Python wrapper | `5.12.1` | `pypdfium2.PYPDFIUM_INFO` |
| PDFium native library | `152.0.7947.0@sourcebuild-native` | `pypdfium2.PDFIUM_INFO` and the installed `version.json` |
| installed `libpdfium.so` | SHA-256 `19efe90ba1edb4c444edbd5d5f12a37590a5a719b7f391e54217dd2d43f85990` | hash of the `linux/arm64` release-candidate file |

The `pypdfium2` distribution installs its PDFium build-license collection,
including PDFium, musl, FreeType, ICU, Little CMS, libjpeg-turbo, OpenJPEG,
libpng, libtiff, zlib, and other applicable notices, under its distribution
metadata `licenses/BUILD_LICENSES` directory. Those files, not this summary,
are the controlling bundled-dependency notices.

## Native libraries used by Pillow

Pillow's runtime feature API reported the following compiled-in versions in
the same `linux/arm64` Alpine release-candidate image:

| Library or feature | Reported version |
| --- | --- |
| libjpeg-turbo | `3.1.4.1` |
| zlib / zlib-ng | `1.3.2` / `2.3.3` |
| libtiff | `4.7.1` |
| FreeType | `2.14.3` |
| Little CMS | `2.19` |
| WebP | `1.6.0` |
| libavif | `1.4.2` |
| OpenJPEG | `2.5.4` |
| Raqm | `0.10.5` |
| FriBidi | `1.0.16` |
| HarfBuzz | `14.2.1` |

The wheel also contained shared objects for Xau, Xdmcp, Brotli, libbsd,
liblzma, libmd, libpng, SharpYUV, libwebp demux/mux, libxcb, and zstd. Some
filenames expose an ABI version rather than an upstream release version, so
this record does not relabel those ABI numbers as upstream versions. Pillow's
installed `dist-info/licenses/LICENSE` contains its license and incorporated
third-party notices.

This observation is architecture-specific and reproducible from the pinned
lock state; the tagged image's generated SBOM and file hashes are the evidence
for a particular published artifact. Update this record whenever either wheel,
platform target, or bundled native build changes.

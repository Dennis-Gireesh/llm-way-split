# Third-Party Notices and Inventory

WaySplit's original source is licensed under Apache-2.0. No third-party source,
prompt, documentation, visual asset, or statement fixture is copied into this
repository. The application depends on separately licensed packages and system
components installed from their official distributions.

## Direct runtime dependencies

| Component | Version | Declared license |
| --- | --- | --- |
| FastAPI | 0.141.1 | MIT |
| HTTPX | 0.28.1 | BSD-3-Clause |
| Jinja2 | 3.1.6 | BSD-3-Clause |
| Pillow | 12.3.0 | MIT-CMU |
| Pydantic | 2.13.4 | MIT |
| pypdf | 6.15.0 | BSD-3-Clause |
| pypdfium2 | 5.12.1 | BSD-3-Clause, Apache-2.0, and bundled dependency licenses |
| python-multipart | 0.0.32 | Apache-2.0 |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| pytesseract | 0.3.13 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| Tenacity | 9.1.4 | Apache-2.0 |
| Typer | 0.27.1 | MIT |
| Uvicorn | 0.52.1 | BSD-3-Clause |

`pypdfium2` distributes its PDFium binary and platform-specific build-license
files inside the installed package. The project does not remove or replace
them. Pillow wheels also contain native codec and rendering libraries. The
observed versions and architecture-specific provenance are recorded in
[`docs/EMBEDDED_NATIVE_COMPONENTS.md`](docs/EMBEDDED_NATIVE_COMPONENTS.md).

## Container components

The default image is built from the digest-pinned official Python Alpine image
and installs exact Alpine versions of Tesseract OCR plus English language data.
Alpine packages retain the copyright/license records shipped in the image.
Tesseract is Apache-2.0; its runtime libraries retain their own licenses.

The optional Ollama container and every downloaded model are separate works.
No model is bundled by WaySplit. Operators must review each model's license and
redistribution/use restrictions.

## Release inventory and evidence

This file is a human-readable orientation, not a substitute for dependency
license texts. Tagged releases publish CycloneDX and SPDX JSON SBOMs for the
release container plus checksums and an immutable image digest. The security
workflow also retains image/filesystem scan evidence. Scanner-generated SBOMs
can omit or incompletely identify libraries embedded in wheels and compiled
extensions; the embedded-native record above is the maintained complement, not
a claim that any single inventory is exhaustive.

Package license files remain in their wheels or installed image locations. See
[`docs/PROVENANCE.md`](docs/PROVENANCE.md) for the project's clean-room and
future-reuse policy.

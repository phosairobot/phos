# License audit inventory

This is a repository inventory prepared for the Apache-2.0 adoption. It is not
legal advice, a complete software bill of materials, or a representation that
all dependencies are license-compatible. License texts and obligations must be
verified from the exact distribution that a release includes.

## First-party status

The repository did not document a copyright holder when this audit was made.
The root `NOTICE` therefore uses `[COPYRIGHT HOLDER]`; a maintainer must confirm
the owner and replace that placeholder before publication. Git author names are
not by themselves proof of copyright ownership or contribution authority.

Repository-owned Python files under `src/robot/` carry an Apache-2.0 SPDX
identifier. This does not relicense vendored assets, external model files, or
contributions whose authorization has not been established.

## Bundled and external material

| Component | Detected license | Evidence | Concern / follow-up |
| --- | --- | --- | --- |
| Swagger UI 5.x (`swagger-ui-bundle.js`, `swagger-ui.css`) | Apache-2.0 | `src/robot/web/static/swagger-ui/NOTICE.md` | Vendored files and their local notice are retained. Verify the exact upstream distribution and retain any upstream NOTICE material when updating. |
| `swagger-init.js` integration file | Not established by this audit | Located beside Swagger UI assets; no individual attribution found | Determine whether this is PHOS-authored glue or an upstream file before adding or changing a header. |
| OpenCV Zoo expression ONNX model | Apache-2.0 stated by upstream documentation | `docs/vision-model-evaluation.md` and installation instructions | Model is downloaded by operators and is not bundled in this repository. Retain its upstream license with deployed model artifacts. |
| OpenCV Zoo NanoDet ONNX model | See upstream model license | `docs/installation.md`, `docs/mcp.md` | Operator-installed model; confirm exact upstream licensing and notices before distribution. |

## Python dependencies

The manifests name the components below but do not embed their license texts.
The detected-license column is intentionally `Not verified from this
repository` unless a local notice establishes it. Resolve each exact installed
version against its upstream package metadata and license file before a release.

| Component group | Components named in manifests | Detected license | Source | Concern / follow-up |
| --- | --- | --- | --- | --- |
| Core | PyYAML, cryptography | Not verified from this repository | `pyproject.toml` | Review exact release metadata, transitive dependencies, notices, and export/cryptography obligations. |
| Hardware and sensors | smbus2, rpi-ws281x, pyserial, RPi.bme280, bmp280 | Not verified from this repository | `pyproject.toml` | Verify optional-package licenses and any Raspberry Pi OS packages used in production. |
| Web | Flask, Flask-WTF, Werkzeug, waitress, qrcode and pinned transitive packages | Not verified from this repository | `pyproject.toml`, `requirements-web.txt` | Generate a version-pinned dependency and license inventory for the release environment. |
| Cloud and voice | boto3, audioop-lts, cartesia, elevenlabs, piper-tts, vosk | Not verified from this repository | `pyproject.toml` | Review SDK licenses, service terms, model licenses, and transitive dependencies separately. |
| Vision | picamera2 and Raspberry Pi OS OpenCV packages | Not verified from this repository | `pyproject.toml`, `requirements.txt` | Review OS-package licenses and the model assets independently; do not assume PyPI metadata covers system packages. |
| MCP | mcp | Not verified from this repository | `pyproject.toml` | Verify the selected Python-version-compatible package and its transitive dependencies. |
| Documentation | mkdocs, mkdocs-material | Not verified from this repository | `requirements-docs.txt` | Verify build-time package licenses and theme assets used in published documentation. |

## Repository delivery boundaries

- No `package.json`, `platformio.ini`, ESP32 firmware source, or Git submodule
  was present in this checkout at audit time.
- The official ESP32 firmware is maintained at
  [phosairobot/phos-esp32](https://github.com/phosairobot/phos-esp32); firmware
  license and dependency review must occur in that source repository.
- Python dependency ranges are not a release inventory. Before publishing a
  binary image, wheel, container, or installer, produce a locked inventory for
  that artifact and preserve required notices.

# PHOS 1.2.0 release record

The authoritative version is `robot.__version__` in `src/robot/__init__.py`.
Setuptools packaging, startup logging, Web Admin status, and the documentation
homepage derive from that source.

## Scope

PHOS 1.2.0 adds the authenticated, provider-neutral Remote API milestone to the
implemented 1.1 environmental and motion baseline. It includes semantic status,
commands, capability discovery, overlay arbitration, local OpenAPI/Swagger
documentation, and a bounded SSE event stream. The API does not expose device
drivers, GPIO, camera frames, or renderer internals.

See the [Remote API guide](remote-api.md) and checked-in
[OpenAPI contract](api/openapi.yaml) for the supported interface.

## Release gates

- Run the hardware-free regression suite.
- Build the documentation with `mkdocs build --strict`.
- Repeat target Raspberry Pi display, sensor, and LAN access verification before
  creating a release tag.

PHOS 1.1.0 remains an historical release record; its test details are
preserved in [the 1.1.0 record](release-1.1.0.md).

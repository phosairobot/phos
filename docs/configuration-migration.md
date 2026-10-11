# JSON to YAML configuration migration

`config/phos.yaml` is PHOS's preferred configuration format. PHOS also supports
`phos.yml` and legacy `phos.json`; discovery selects YAML, then YML, then JSON.
An explicit `--config` path always wins.

Existing JSON installations continue to work and do not need to migrate
immediately. PHOS never converts a configuration file automatically.

## Recommended migration

1. Stop PHOS.
2. Back up `config/phos.json`.
3. Convert the complete configuration to `config/phos.yaml`, preserving every
   value and required section.
4. Edit YAML with spaces, not tabs; keep booleans, numbers and null values as
   their intended scalar types. Do not use YAML object tags.
5. Start PHOS without `--config` and verify the startup log reports
   `config/phos.yaml` as the active source.
6. Save once in Web Admin and confirm it updates `phos.yaml`.
7. Keep the JSON backup until physical and Web Admin validation are complete.

All formats still receive the same strict `RuntimeConfig` validation. Web Admin
persists to the source active at startup: YAML remains YAML and JSON remains JSON.

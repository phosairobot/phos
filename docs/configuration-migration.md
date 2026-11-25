# JSON to YAML configuration migration

PHOS uses YAML as its only supported runtime configuration format. Normal PHOS
startup never reads, converts, or overwrites a JSON configuration file.

For an older installation that still has `phos.json`, stop PHOS and run the
explicit one-time migration from the checkout:

```bash
PYTHONPATH=src .venv/bin/python tools/migrate_phos_json_to_yaml.py \
  config/phos.json config/phos.yaml
```

The destination must not already exist. The tool validates the complete source
document through `RuntimeConfig`, preserves configuration-relative paths at the
new YAML location, writes atomically, and leaves the original JSON source in
place as the backup. Correct any reported validation error before starting
PHOS with the new YAML file:

```bash
PYTHONPATH=src .venv/bin/python -m robot.main --config config/phos.yaml
```

JSON remains in PHOS API, MCP, and `phos-hw` JSON Lines protocol contracts;
this migration applies only to the runtime configuration file.

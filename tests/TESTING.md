# Testing

This repo includes small Python helpers that let you catch blueprint problems before importing into Home Assistant.

## Install dependencies

```bash
python -m pip install -r tests/requirements-dev.txt
```

## Run checks

### 1. Validate blueprint YAML structure

```bash
python tests/check_blueprint_yaml.py
```

This checks:
- YAML parsing with Home Assistant `!input` tags
- Required blueprint blocks such as `blueprint`, `variables`, `trigger`, and `action`
- Required inputs and expected `choose` template branches

### 2. Run template smoke tests

```bash
python tests/smoke_test_blueprint.py
```

The output shows each scenario separately with `PASS` or `FAIL`, followed by a summary for each blueprint file.

If a scenario covers a known unsupported behavior, the script prints `XFAIL` instead. That means the case is tracked intentionally, but it does not fail the whole run.

This checks:
- Key template variables such as `effective_hours`, `threshold`, and `current_slot_price`
- Action conditions for cheap and expensive branches
- Hourly and 15-minute Nordpool scenarios
- Dynamic cheap-hours override behavior
- Absolute threshold behavior
- Allowed and blocked weekdays
- Start-time and end-time boundary behavior
- Equal-to-threshold cheap behavior
- Expensive branch selection inside and outside the time window
- Fallback current-price lookup when `raw_today` is unavailable
- Zero-hour and oversized-hour threshold behavior
- Empty Nordpool data fallback behavior
- `raw_tomorrow` current-slot resolution
- Negative current prices remain valid input data

These tests are logic-only checks against mocked Home Assistant helpers and mocked Nordpool sensor data. They do not edit or rewrite the real blueprint templates.

## What these tests do not replace

- Final validation inside Home Assistant
- Home Assistant runtime behavior outside the mocked helpers
- Real automation execution against your live entities

## Recommended workflow

1. Run the Python checks locally.
2. Re-import the blueprint into Home Assistant.
3. Validate templates in Developer Tools.
4. Test with a real automation and your actual Nordpool sensor.
from __future__ import annotations

import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLUEPRINTS = sorted(ROOT.glob("nordpool_cheap_prices*.yaml"))


class BlueprintLoader(yaml.SafeLoader):
    pass


def _construct_input(loader: BlueprintLoader, node: yaml.Node) -> dict[str, str]:
    return {"__ha_input__": loader.construct_scalar(node)}


BlueprintLoader.add_constructor("!input", _construct_input)


def load_blueprint(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=BlueprintLoader)


def validate_blueprint(path: Path) -> list[str]:
    data = load_blueprint(path)
    issues: list[str] = []

    required_top_level = ["blueprint", "variables", "trigger", "action"]
    for key in required_top_level:
        if key not in data:
            issues.append(f"missing top-level key: {key}")

    blueprint_block = data.get("blueprint", {})
    if blueprint_block.get("domain") != "automation":
        issues.append("blueprint.domain must be automation")

    input_block = blueprint_block.get("input", {})
    required_inputs = {
        "grid_area",
        "cheap_hours",
        "dynamic_hours_sensor",
        "start_time",
        "end_time",
        "use_absolute_threshold",
        "absolute_price_threshold",
        "run_days",
        "cheap",
        "expensive",
    }
    missing_inputs = sorted(required_inputs - set(input_block))
    if missing_inputs:
        issues.append(f"missing inputs: {', '.join(missing_inputs)}")

    variables = data.get("variables", {})
    for name in ["effective_hours", "threshold", "current_slot_price"]:
        if name not in variables:
            issues.append(f"missing variable: {name}")

    choose_blocks = []
    for action in data.get("action", []):
        choose_blocks.extend(action.get("choose", []))

    if len(choose_blocks) != 3:
        issues.append(f"expected 3 choose branches, found {len(choose_blocks)}")

    for index, choose in enumerate(choose_blocks, start=1):
        conditions = choose.get("conditions", [])
        if not conditions:
            issues.append(f"choose branch {index} has no conditions")
            continue
        template = conditions[0].get("value_template")
        if not isinstance(template, str) or "{{" not in template:
            issues.append(f"choose branch {index} has no template condition")

    return issues


def main() -> int:
    targets = [Path(arg).resolve() for arg in sys.argv[1:]] or DEFAULT_BLUEPRINTS
    failed = False

    for target in targets:
        issues = validate_blueprint(target)
        if issues:
            failed = True
            print(f"FAIL {target.name}")
            for issue in issues:
                print(f"  - {issue}")
            continue

        print(f"PASS {target.name}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
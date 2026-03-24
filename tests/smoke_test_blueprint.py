from __future__ import annotations

import ast
import math
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLUEPRINTS = sorted(ROOT.glob("nordpool_cheap_prices*.yaml"))


@dataclass(frozen=True)
class InputRef:
    name: str


@dataclass(frozen=True)
class Scenario:
    name: str
    now_value: datetime
    inputs: dict[str, Any]
    entity_states: dict[str, str]
    entity_attrs: dict[str, dict[str, Any]]
    expected_variables: dict[str, Any]
    expected_conditions: list[bool]
    known_limitation_reason: str | None = None


@dataclass(frozen=True)
class ScenarioResult:
    scenario_name: str
    errors: list[str]
    known_limitation_reason: str | None = None

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def is_known_limitation(self) -> bool:
        return bool(self.errors and self.known_limitation_reason)


class BlueprintLoader(yaml.SafeLoader):
    pass


def _construct_input(loader: BlueprintLoader, node: yaml.Node) -> InputRef:
    return InputRef(loader.construct_scalar(node))


BlueprintLoader.add_constructor("!input", _construct_input)


def load_blueprint(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=BlueprintLoader)


class MockHomeAssistant:
    def __init__(self, now_value: datetime, entity_states: dict[str, str], entity_attrs: dict[str, dict[str, Any]]) -> None:
        self._now_value = now_value
        self._entity_states = entity_states
        self._entity_attrs = entity_attrs

    def now(self) -> datetime:
        return self._now_value

    def states(self, entity_id: str) -> str:
        return self._entity_states.get(entity_id, "unknown")

    def state_attr(self, entity_id: str, attribute: str) -> Any:
        return self._entity_attrs.get(entity_id, {}).get(attribute)

    @staticmethod
    def as_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


def build_environment() -> Environment:
    return Environment(
        trim_blocks=True,
        lstrip_blocks=True,
        extensions=["jinja2.ext.loopcontrols"],
    )


def is_template(value: Any) -> bool:
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def normalize_rendered(value: str) -> Any:
    text = value.strip()
    if text == "":
        return ""

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    try:
        return int(text)
    except ValueError:
        pass

    try:
        return float(text)
    except ValueError:
        pass

    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text


def render_value(env: Environment, template: str, context: dict[str, Any]) -> Any:
    rendered = env.from_string(template).render(context)
    return normalize_rendered(rendered)


def compare_values(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected


def hourly_prices() -> list[float]:
    return [float(value) for value in range(24)]


def quarter_hour_prices() -> list[float]:
    return [float(value) for value in range(96)]


def repeated_hourly_prices(hour_values: list[float]) -> list[float]:
    if len(hour_values) != 24:
        raise ValueError("hour_values must contain exactly 24 items")
    return [float(value) for value in hour_values]


def repeated_quarter_hour_prices(hour_values: list[float]) -> list[float]:
    if len(hour_values) != 24:
        raise ValueError("hour_values must contain exactly 24 items")

    expanded: list[float] = []
    for value in hour_values:
        expanded.extend([float(value)] * 4)
    return expanded


def build_raw_prices(prices: list[float], start: datetime, step_minutes: int) -> list[dict[str, Any]]:
    rows = []
    for index, value in enumerate(prices):
        slot_start = start + timedelta(minutes=index * step_minutes)
        slot_end = slot_start + timedelta(minutes=step_minutes)
        rows.append(
            {
                "start": slot_start.isoformat(),
                "end": slot_end.isoformat(),
                "value": value,
            }
        )
    return rows


SCENARIOS = [
    Scenario(
        name="hourly-cheap-window",
        now_value=datetime.fromisoformat("2026-03-23T02:10:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 3,
            "dynamic_hours_sensor": "",
            "start_time": "00:00:00",
            "end_time": "23:59:59",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": hourly_prices(),
                "raw_today": build_raw_prices(hourly_prices(), datetime.fromisoformat("2026-03-23T00:00:00+02:00"), 60),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "effective_hours": 3,
            "today": 0,
            "in_time_window": True,
            "threshold": 3.0,
            "current_slot_price": 2.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="quarter-hour-dynamic-hours",
        now_value=datetime.fromisoformat("2026-03-23T01:15:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 5,
            "dynamic_hours_sensor": "input_number.cheap_hours_runtime",
            "start_time": "01:00:00",
            "end_time": "02:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={"input_number.cheap_hours_runtime": "2"},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": quarter_hour_prices(),
                "raw_today": build_raw_prices(quarter_hour_prices(), datetime.fromisoformat("2026-03-23T00:00:00+02:00"), 15),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "effective_hours": 2,
            "today": 0,
            "in_time_window": True,
            "threshold": 8.0,
            "current_slot_price": 5.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="absolute-threshold-bypass",
        now_value=datetime.fromisoformat("2026-03-24T12:30:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 1,
            "dynamic_hours_sensor": "",
            "start_time": "00:00:00",
            "end_time": "01:00:00",
            "use_absolute_threshold": True,
            "absolute_price_threshold": 62,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": [float(value) for value in range(50, 74)],
                "raw_today": build_raw_prices([float(value) for value in range(50, 74)], datetime.fromisoformat("2026-03-24T00:00:00+02:00"), 60),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 1,
            "in_time_window": False,
            "current_slot_price": 62.0,
        },
        expected_conditions=[True, False, False],
    ),
    Scenario(
        name="outside-window-expensive",
        now_value=datetime.fromisoformat("2026-03-23T23:30:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "22:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": hourly_prices(),
                "raw_today": build_raw_prices(hourly_prices(), datetime.fromisoformat("2026-03-23T00:00:00+02:00"), 60),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 0,
            "in_time_window": False,
            "current_slot_price": 23.0,
        },
        expected_conditions=[False, False, True],
    ),
    Scenario(
        name="weekday-disabled-no-action",
        now_value=datetime.fromisoformat("2026-03-24T08:15:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 4,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "10:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([50, 48, 45, 42, 40, 35, 20, 10, 12, 14, 30, 32, 40, 45, 50, 60, 65, 70, 72, 68, 64, 58, 55, 53]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([50, 48, 45, 42, 40, 35, 20, 10, 12, 14, 30, 32, 40, 45, 50, 60, 65, 70, 72, 68, 64, 58, 55, 53]),
                    datetime.fromisoformat("2026-03-24T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 1,
            "in_time_window": True,
            "threshold": 30.0,
            "current_slot_price": 12.0,
        },
        expected_conditions=[False, False, False],
    ),
    Scenario(
        name="start-boundary-cheap",
        now_value=datetime.fromisoformat("2026-03-23T06:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "08:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([40, 38, 36, 34, 32, 30, 5, 10, 25, 35, 45, 55, 60, 58, 56, 54, 52, 50, 48, 46, 44, 42, 41, 39]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([40, 38, 36, 34, 32, 30, 5, 10, 25, 35, 45, 55, 60, 58, 56, 54, 52, 50, 48, 46, 44, 42, 41, 39]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 0,
            "in_time_window": True,
            "threshold": 25.0,
            "current_slot_price": 5.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="end-boundary-cheap",
        now_value=datetime.fromisoformat("2026-03-23T08:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "08:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([40, 38, 36, 34, 32, 30, 5, 10, 25, 35, 45, 55, 60, 58, 56, 54, 52, 50, 48, 46, 44, 42, 41, 39]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([40, 38, 36, 34, 32, 30, 5, 10, 25, 35, 45, 55, 60, 58, 56, 54, 52, 50, 48, 46, 44, 42, 41, 39]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 0,
            "in_time_window": True,
            "threshold": 25.0,
            "current_slot_price": 25.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="inside-window-expensive",
        now_value=datetime.fromisoformat("2026-03-23T07:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 1,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "09:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 30, 20, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 30, 20, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 0,
            "in_time_window": True,
            "threshold": 20.0,
            "current_slot_price": 30.0,
        },
        expected_conditions=[False, False, True],
    ),
    Scenario(
        name="equal-threshold-is-cheap",
        now_value=datetime.fromisoformat("2026-03-23T08:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "09:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 20, 20, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 20, 20, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 0,
            "in_time_window": True,
            "threshold": 20.0,
            "current_slot_price": 20.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="dynamic-hours-fallback-unknown-state",
        now_value=datetime.fromisoformat("2026-03-23T07:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "input_number.cheap_hours_runtime",
            "start_time": "06:00:00",
            "end_time": "09:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={"input_number.cheap_hours_runtime": "unknown"},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 20, 30, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 20, 30, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "effective_hours": 2,
            "threshold": 30.0,
            "current_slot_price": 20.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="quarter-hour-threshold-window",
        now_value=datetime.fromisoformat("2026-03-23T01:45:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 1,
            "dynamic_hours_sensor": "",
            "start_time": "01:00:00",
            "end_time": "02:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_quarter_hour_prices([80, 20, 50, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270, 280, 290]),
                "raw_today": build_raw_prices(
                    repeated_quarter_hour_prices([80, 20, 50, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270, 280, 290]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    15,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 0,
            "in_time_window": True,
            "threshold": 50.0,
            "current_slot_price": 20.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="current-price-fallback-without-raw-data",
        now_value=datetime.fromisoformat("2026-03-23T14:30:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 3,
            "dynamic_hours_sensor": "",
            "start_time": "12:00:00",
            "end_time": "16:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_quarter_hour_prices([90, 88, 86, 84, 82, 80, 78, 76, 74, 72, 70, 68, 30, 10, 20, 40, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": [],
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "today": 0,
            "in_time_window": True,
            "threshold": 40.0,
            "current_slot_price": 20.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="zero-cheap-hours-minimum-only",
        now_value=datetime.fromisoformat("2026-03-23T07:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 0,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "09:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([60, 58, 56, 54, 52, 50, 15, 20, 25, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([60, 58, 56, 54, 52, 50, 15, 20, 25, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "effective_hours": 0,
            "threshold": 15.0,
            "current_slot_price": 20.0,
        },
        expected_conditions=[False, False, True],
    ),
    Scenario(
        name="cheap-hours-greater-than-window-clamps-max",
        now_value=datetime.fromisoformat("2026-03-23T07:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 10,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "08:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([60, 58, 56, 54, 52, 50, 15, 20, 25, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([60, 58, 56, 54, 52, 50, 15, 20, 25, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "threshold": 25.0,
            "current_slot_price": 20.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="no-run-days-no-actions",
        now_value=datetime.fromisoformat("2026-03-23T07:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "09:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": [],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 20, 30, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([60, 58, 56, 54, 52, 50, 10, 20, 30, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "days": [False, False, False, False, False, False, False],
            "current_slot_price": 20.0,
        },
        expected_conditions=[False, False, False],
    ),
    Scenario(
        name="empty-prices-fallback-to-9999",
        now_value=datetime.fromisoformat("2026-03-23T07:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "09:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": [],
                "raw_today": [],
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "threshold": 9999,
            "current_slot_price": 9999.0,
        },
        expected_conditions=[False, True, False],
    ),
    Scenario(
        name="negative-current-price-is-valid",
        now_value=datetime.fromisoformat("2026-03-23T07:00:00+02:00"),
        inputs={
            "grid_area": "sensor.nordpool_mock",
            "cheap_hours": 2,
            "dynamic_hours_sensor": "",
            "start_time": "06:00:00",
            "end_time": "09:00:00",
            "use_absolute_threshold": False,
            "absolute_price_threshold": 0,
            "run_days": ["mon"],
        },
        entity_states={},
        entity_attrs={
            "sensor.nordpool_mock": {
                "today": repeated_hourly_prices([60, 58, 56, 54, 52, 50, -15, -5, 10, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                "raw_today": build_raw_prices(
                    repeated_hourly_prices([60, 58, 56, 54, 52, 50, -15, -5, 10, 40, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74]),
                    datetime.fromisoformat("2026-03-23T00:00:00+02:00"),
                    60,
                ),
                "raw_tomorrow": [],
            }
        },
        expected_variables={
            "effective_hours": 2,
            "threshold": 10.0,
            "current_slot_price": -5.0,
        },
        expected_conditions=[False, True, False],
    ),
]


def evaluate_blueprint(path: Path, scenario: Scenario) -> ScenarioResult:
    try:
        blueprint = load_blueprint(path)
        mock_ha = MockHomeAssistant(scenario.now_value, scenario.entity_states, scenario.entity_attrs)
        env = build_environment()
        context: dict[str, Any] = {
            "now": mock_ha.now,
            "states": mock_ha.states,
            "state_attr": mock_ha.state_attr,
            "as_datetime": mock_ha.as_datetime,
            "none": None,
            "true": True,
            "false": False,
        }
        resolved_variables: dict[str, Any] = {}

        for name, raw_value in blueprint.get("variables", {}).items():
            if isinstance(raw_value, InputRef):
                value = scenario.inputs[raw_value.name]
            elif is_template(raw_value):
                value = render_value(env, raw_value, context)
            else:
                value = raw_value

            resolved_variables[name] = value
            context[name] = value

        errors: list[str] = []
        for variable_name, expected_value in scenario.expected_variables.items():
            actual_value = resolved_variables.get(variable_name)
            if not compare_values(actual_value, expected_value):
                errors.append(
                    f"scenario {scenario.name}: variable {variable_name} expected {expected_value!r}, got {actual_value!r}"
                )

        action_blocks = blueprint.get("action", [])
        choose_blocks = action_blocks[0].get("choose", []) if action_blocks else []
        actual_conditions: list[bool] = []
        for choose in choose_blocks:
            condition = choose.get("conditions", [])[0]
            result = render_value(env, condition["value_template"], context)
            actual_conditions.append(bool(result))

        if actual_conditions != scenario.expected_conditions:
            errors.append(
                f"scenario {scenario.name}: conditions expected {scenario.expected_conditions!r}, got {actual_conditions!r}"
            )

        return ScenarioResult(
            scenario_name=scenario.name,
            errors=errors,
            known_limitation_reason=scenario.known_limitation_reason,
        )
    except Exception as exc:
        return ScenarioResult(
            scenario_name=scenario.name,
            errors=[
                f"scenario {scenario.name}: raised {type(exc).__name__}: {exc}",
                traceback.format_exc().strip(),
            ],
            known_limitation_reason=scenario.known_limitation_reason,
        )


def main() -> int:
    targets = [Path(arg).resolve() for arg in sys.argv[1:]] or DEFAULT_BLUEPRINTS
    failures: list[str] = []

    for target in targets:
        scenario_results: list[ScenarioResult] = []
        print(f"FILE {target.name}")
        for scenario in SCENARIOS:
            result = evaluate_blueprint(target, scenario)
            scenario_results.append(result)

            if result.passed:
                print(f"  PASS {result.scenario_name}")
                continue

            if result.is_known_limitation:
                print(f"  XFAIL {result.scenario_name}")
                print(f"    - known limitation: {result.known_limitation_reason}")
                for error in result.errors:
                    print(f"    - {error}")
                continue

            print(f"  FAIL {result.scenario_name}")
            for error in result.errors:
                print(f"    - {error}")

        failed_results = [result for result in scenario_results if not result.passed and not result.is_known_limitation]
        xfailed_results = [result for result in scenario_results if result.is_known_limitation]
        passed_count = len([result for result in scenario_results if result.passed])

        print(
            f"SUMMARY {target.name}: {passed_count} passed, {len(xfailed_results)} known limitations, {len(failed_results)} failed"
        )

        if failed_results:
            failures.append(target.name)
            print(f"RESULT FAIL {target.name}")
            continue

        if xfailed_results:
            print(f"RESULT PASS-WITH-KNOWN-LIMITATIONS {target.name}")
            continue

        print(f"RESULT PASS {target.name}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
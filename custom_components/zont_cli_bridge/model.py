"""Pure protocol validation. No HA imports, network access or side effects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any

from .const import BOILER_SECONDS, FUTURE_SECONDS, HEARTBEAT_SECONDS, SOURCE_SECONDS

BOILER_NUMBERS = {
    "supply_temperature": (-50.0, 150.0),
    "return_temperature": (-50.0, 150.0),
    "target_temperature": (0.0, 150.0),
    "circuit_water_request": (0.0, 150.0),
    "burner_modulation": (0.0, 100.0),
}
OPERATION_OPTIONS = ["heating", "dhw", "heating_and_dhw", "heating_without_flame", "dhw_without_flame", "both_without_flame", "idle", "flame_only"]


def number(value: Any, minimum: float, maximum: float) -> float | None:
    """Accept actual finite numbers, excluding booleans and numeric strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and minimum <= value <= maximum else None


def timestamp(value: Any) -> datetime | None:
    """Parse only timezone-aware ISO timestamps."""
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def fresh(value: Any, now: datetime, seconds: int) -> bool:
    parsed = timestamp(value)
    if parsed is None:
        return False
    return -FUTURE_SECONDS <= (now - parsed).total_seconds() <= seconds


def operation(flame: Any, heating: Any, dhw: Any) -> str | None:
    """Report only explicit flags; missing flags are not off."""
    if not all(isinstance(value, bool) for value in (flame, heating, dhw)):
        return None
    if heating and dhw:
        return "heating_and_dhw" if flame else "both_without_flame"
    if heating:
        return "heating" if flame else "heating_without_flame"
    if dhw:
        return "dhw" if flame else "dhw_without_flame"
    return "flame_only" if flame else "idle"


def room_action(flame: Any, heating: Any, dhw: Any) -> str | None:
    """Do not infer space-heating demand from a flame during DHW."""
    if not all(isinstance(value, bool) for value in (flame, heating, dhw)):
        return None
    if flame and heating and not dhw:
        return "heating"
    if not flame or not heating:
        return "idle"
    return None


@dataclass(frozen=True)
class BridgeSnapshot:
    """Sanitized current observations, re-evaluated as wall time advances."""
    heartbeat_fresh: bool = False
    control_available: bool = False
    boiler_available: bool = False
    bridge_state: str | None = None
    phase: str | None = None
    current_temperature: float | None = None
    room_target: float | None = None
    boiler_values: dict[str, float | bool | str | datetime | None] = field(default_factory=dict)
    room_hvac_action: str | None = None
    heartbeat: datetime | None = None
    boiler_read_at: datetime | None = None
    controller_received_at: datetime | None = None


def project(attributes: Any, state: str | None, now: datetime) -> BridgeSnapshot:
    """Validate protocol v1 without extending timestamps on repeated HA reads."""
    if not isinstance(attributes, dict):
        return BridgeSnapshot()
    alive = fresh(attributes.get("heartbeat"), now, HEARTBEAT_SECONDS)
    active = attributes.get("phase") == "active"
    blocked = attributes.get("configuration_block") is not None
    current = None
    if (
        alive
        and state == "delivery_verified"
        and fresh(attributes.get("last_verified_delivery"), now, HEARTBEAT_SECONDS)
        and fresh(attributes.get("source_measured_at"), now, SOURCE_SECONDS)
    ):
        current = number(attributes.get("source_temperature"), 5.0, 35.0)
    boiler = attributes.get("boiler")
    schema_ok = isinstance(boiler, dict) and type(boiler.get("schema_version")) is int and boiler["schema_version"] == 1
    if not schema_ok:
        boiler = {}
    available = (
        alive
        and schema_ok
        and boiler.get("online") is True
        and boiler.get("adapter_healthy") is True
        and fresh(boiler.get("read_at"), now, BOILER_SECONDS)
        and fresh(boiler.get("controller_received_at"), now, BOILER_SECONDS)
    )
    values: dict[str, float | bool | str | datetime | None] = {}
    if available:
        values.update({key: number(boiler.get(key), *bounds) for key, bounds in BOILER_NUMBERS.items()})
        for key in ("flame", "heating", "dhw"):
            raw = boiler.get(key)
            values[key] = raw if isinstance(raw, bool) else None
        values["operation"] = operation(values["flame"], values["heating"], values["dhw"])
        values["read_timestamp"] = timestamp(boiler.get("read_at"))
    return BridgeSnapshot(
        heartbeat_fresh=alive,
        control_available=alive and active and not blocked and (
            "control_available" not in attributes or
            (attributes.get("control_available") is True and fresh(attributes.get("control_verified_at"), now, BOILER_SECONDS))
        ),
        boiler_available=bool(available),
        bridge_state=state if isinstance(state, str) and len(state) <= 100 else None,
        phase=attributes.get("phase") if attributes.get("phase") in ("active", "preflight", "read_only") else None,
        current_temperature=current,
        room_target=number(attributes.get("actual_target"), 5.0, 30.0) if alive else None,
        boiler_values=values,
        room_hvac_action=room_action(values.get("flame"), values.get("heating"), values.get("dhw")) if available else None,
        heartbeat=timestamp(attributes.get("heartbeat")),
        boiler_read_at=timestamp(boiler.get("read_at")),
        controller_received_at=timestamp(boiler.get("controller_received_at")),
    )

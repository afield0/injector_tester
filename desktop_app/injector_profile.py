from __future__ import annotations

from dataclasses import dataclass

from .advanced_testing import DeadtimePoint


PROFILE_FORMAT_NAME = "injector_tester_injector_v1"


@dataclass(frozen=True)
class InjectorProfile:
    injector_lb_per_hour: float
    injector_cc_per_min: float
    deadtime_curve: tuple[DeadtimePoint, ...]


def dump_injector_profile(profile: InjectorProfile) -> str:
    lines = [
        f"format={PROFILE_FORMAT_NAME}",
        f"injector_lb_per_hour={profile.injector_lb_per_hour:.2f}",
        f"injector_cc_per_min={profile.injector_cc_per_min:.2f}",
    ]
    lines.extend(
        f"deadtime={point.voltage:.2f},{point.deadtime_ms:.3f}" for point in profile.deadtime_curve
    )
    return "\n".join(lines) + "\n"


def load_injector_profile(text: str) -> InjectorProfile:
    injector_lb_per_hour: float | None = None
    injector_cc_per_min: float | None = None
    deadtime_curve: list[DeadtimePoint] = []
    format_name: str | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Line {line_number}: expected key=value")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if key == "format":
            format_name = value
            continue
        if key == "injector_lb_per_hour":
            injector_lb_per_hour = _parse_positive_float(value, line_number, key)
            continue
        if key == "injector_cc_per_min":
            injector_cc_per_min = _parse_positive_float(value, line_number, key)
            continue
        if key == "deadtime":
            voltage_text, separator, deadtime_text = value.partition(",")
            if not separator:
                raise ValueError(f"Line {line_number}: deadtime must be voltage,deadtime_ms")
            deadtime_curve.append(
                DeadtimePoint(
                    voltage=_parse_positive_float(voltage_text, line_number, "deadtime voltage"),
                    deadtime_ms=_parse_positive_float(deadtime_text, line_number, "deadtime ms"),
                )
            )
            continue
        raise ValueError(f"Line {line_number}: unknown key '{key}'")

    if format_name != PROFILE_FORMAT_NAME:
        raise ValueError(
            f"Unsupported injector profile format: {format_name!r}. Expected {PROFILE_FORMAT_NAME!r}."
        )
    if injector_lb_per_hour is None:
        raise ValueError("Injector profile is missing injector_lb_per_hour")
    if injector_cc_per_min is None:
        raise ValueError("Injector profile is missing injector_cc_per_min")
    if not deadtime_curve:
        raise ValueError("Injector profile must contain at least one deadtime entry")

    return InjectorProfile(
        injector_lb_per_hour=injector_lb_per_hour,
        injector_cc_per_min=injector_cc_per_min,
        deadtime_curve=tuple(deadtime_curve),
    )


def _parse_positive_float(value: str, line_number: int, field_name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"Line {line_number}: {field_name} must be numeric") from exc
    if parsed <= 0.0:
        raise ValueError(f"Line {line_number}: {field_name} must be greater than 0")
    return parsed

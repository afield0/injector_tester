from __future__ import annotations

from dataclasses import dataclass


# Approximate gasoline conversion used to keep the two injector size inputs aligned.
# Keeping it centralized makes the UI sync logic and the calculation path use the
# same assumption.
LB_PER_HOUR_TO_CC_PER_MIN = 10.5
ADVANCED_TEST_MODEL = 0


@dataclass(frozen=True)
class DeadtimePoint:
    """A single injector deadtime calibration point."""

    voltage: float
    deadtime_ms: float


@dataclass(frozen=True)
class AdvancedTestInputs:
    """User-editable inputs required to derive a basic counted test."""

    battery_voltage: float
    desired_fuel_ml: float
    injector_size_cc_per_min: float
    rpm: float
    duration_seconds: float
    deadtime_curve: tuple[DeadtimePoint, ...]


@dataclass(frozen=True)
class AdvancedCalculationResult:
    """Derived test values plus validation state for the advanced tab."""

    model: int
    input_voltage: float
    applied_voltage: float
    voltage_was_clamped: bool
    raw_pulse_count: float
    pulse_count: int
    cycle_time_ms: float
    interpolated_deadtime_ms: float
    effective_open_time_ms: float
    commanded_pulse_width_ms: float
    duty_cycle_percent: float
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def cc_per_min_from_lb_per_hour(lb_per_hour: float) -> float:
    """Convert injector flow from lb/hr to cc/min using the shared constant."""

    return lb_per_hour * LB_PER_HOUR_TO_CC_PER_MIN


def lb_per_hour_from_cc_per_min(cc_per_min: float) -> float:
    """Convert injector flow from cc/min to lb/hr using the shared constant."""

    return cc_per_min / LB_PER_HOUR_TO_CC_PER_MIN


def calculate_advanced_test(inputs: AdvancedTestInputs) -> AdvancedCalculationResult:
    """Calculate a 4-stroke counted test from fuel, flow, RPM, and deadtime data.

    The firmware only accepts an integer pulse count, so the exact event count is
    rounded to the nearest whole pulse before deriving per-pulse fuel delivery.
    """

    errors: list[str] = []
    warnings: list[str] = []

    if inputs.battery_voltage <= 0.0:
        errors.append("Battery voltage must be greater than 0 V.")
    if inputs.desired_fuel_ml <= 0.0:
        errors.append("Desired fuel amount must be greater than 0 mL.")
    if inputs.injector_size_cc_per_min <= 0.0:
        errors.append("Injector size must be greater than 0 cc/min.")
    if inputs.rpm <= 0.0:
        errors.append("RPM must be greater than 0.")
    if inputs.duration_seconds <= 0.0:
        errors.append("Test duration must be greater than 0 seconds.")

    normalized_curve = _normalize_deadtime_curve(inputs.deadtime_curve, errors)

    if errors:
        return AdvancedCalculationResult(
            model=ADVANCED_TEST_MODEL,
            input_voltage=inputs.battery_voltage,
            applied_voltage=inputs.battery_voltage,
            voltage_was_clamped=False,
            raw_pulse_count=0.0,
            pulse_count=0,
            cycle_time_ms=0.0,
            interpolated_deadtime_ms=0.0,
            effective_open_time_ms=0.0,
            commanded_pulse_width_ms=0.0,
            duty_cycle_percent=0.0,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    interpolated_deadtime_ms, applied_voltage, voltage_was_clamped = interpolate_deadtime(
        inputs.battery_voltage,
        normalized_curve,
    )
    if voltage_was_clamped:
        warnings.append(
            "Battery voltage is outside the deadtime curve range; deadtime was clamped to the nearest curve value."
        )

    # Advanced mode always derives a 4-stroke test.
    raw_pulse_count = (inputs.rpm / 120.0) * inputs.duration_seconds
    if raw_pulse_count < 1.0:
        errors.append("RPM and test duration must produce at least one injector event.")

    pulse_count = int(round(raw_pulse_count))
    if raw_pulse_count >= 1.0 and pulse_count < 1:
        pulse_count = 1
    if raw_pulse_count >= 1.0 and abs(raw_pulse_count - pulse_count) > 1e-6:
        warnings.append(
            "Pulse count was rounded to the nearest whole pulse to match the firmware counted-run interface."
        )

    cycle_time_ms = 120000.0 / inputs.rpm
    effective_open_time_ms = (
        inputs.desired_fuel_ml * 60000.0 / inputs.injector_size_cc_per_min / pulse_count
        if pulse_count > 0
        else 0.0
    )
    commanded_pulse_width_ms = interpolated_deadtime_ms + effective_open_time_ms
    duty_cycle_percent = (
        commanded_pulse_width_ms / cycle_time_ms * 100.0 if cycle_time_ms > 0.0 else 0.0
    )

    if commanded_pulse_width_ms <= interpolated_deadtime_ms:
        errors.append("Commanded pulse width must be greater than deadtime.")
    if duty_cycle_percent >= 100.0:
        errors.append("Calculated duty cycle is 100% or higher, so the test cannot run.")
    elif duty_cycle_percent > 85.0:
        warnings.append("Calculated duty cycle exceeds 85%; injector control may be marginal.")

    return AdvancedCalculationResult(
        model=ADVANCED_TEST_MODEL,
        input_voltage=inputs.battery_voltage,
        applied_voltage=applied_voltage,
        voltage_was_clamped=voltage_was_clamped,
        raw_pulse_count=raw_pulse_count,
        pulse_count=pulse_count,
        cycle_time_ms=cycle_time_ms,
        interpolated_deadtime_ms=interpolated_deadtime_ms,
        effective_open_time_ms=effective_open_time_ms,
        commanded_pulse_width_ms=commanded_pulse_width_ms,
        duty_cycle_percent=duty_cycle_percent,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def interpolate_deadtime(
    battery_voltage: float,
    deadtime_curve: tuple[DeadtimePoint, ...],
) -> tuple[float, float, bool]:
    """Interpolate deadtime from the voltage curve, clamping outside the range."""

    if len(deadtime_curve) == 1:
        point = deadtime_curve[0]
        return point.deadtime_ms, point.voltage, battery_voltage != point.voltage

    if battery_voltage <= deadtime_curve[0].voltage:
        point = deadtime_curve[0]
        return point.deadtime_ms, point.voltage, True
    if battery_voltage >= deadtime_curve[-1].voltage:
        point = deadtime_curve[-1]
        return point.deadtime_ms, point.voltage, True

    for low_point, high_point in zip(deadtime_curve, deadtime_curve[1:]):
        if low_point.voltage <= battery_voltage <= high_point.voltage:
            span = high_point.voltage - low_point.voltage
            fraction = (battery_voltage - low_point.voltage) / span
            deadtime_ms = low_point.deadtime_ms + (
                (high_point.deadtime_ms - low_point.deadtime_ms) * fraction
            )
            return deadtime_ms, battery_voltage, False

    # The range checks above should make this unreachable, but keep a stable fallback.
    point = deadtime_curve[-1]
    return point.deadtime_ms, point.voltage, True


def default_deadtime_curve() -> tuple[DeadtimePoint, ...]:
    """Return a small editable default curve for the advanced testing tab."""

    return (
        DeadtimePoint(voltage=8.0, deadtime_ms=1.30),
        DeadtimePoint(voltage=10.0, deadtime_ms=1.05),
        DeadtimePoint(voltage=12.0, deadtime_ms=0.90),
        DeadtimePoint(voltage=14.0, deadtime_ms=0.78),
        DeadtimePoint(voltage=16.0, deadtime_ms=0.70),
    )


def _normalize_deadtime_curve(
    deadtime_curve: tuple[DeadtimePoint, ...],
    errors: list[str],
) -> tuple[DeadtimePoint, ...]:
    """Validate and sort the curve so interpolation can assume ordered points."""

    if not deadtime_curve:
        errors.append("Deadtime curve must contain at least one row.")
        return ()

    normalized = sorted(deadtime_curve, key=lambda point: point.voltage)
    seen_voltages: set[float] = set()
    for point in normalized:
        if point.voltage <= 0.0:
            errors.append("Deadtime curve voltages must be greater than 0 V.")
            break
        if point.deadtime_ms <= 0.0:
            errors.append("Deadtime values must be greater than 0 ms.")
            break
        if point.voltage in seen_voltages:
            errors.append("Deadtime curve voltages must be unique.")
            break
        seen_voltages.add(point.voltage)
    return tuple(normalized)

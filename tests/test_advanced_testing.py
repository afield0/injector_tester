from __future__ import annotations

import unittest

from desktop_app.advanced_testing import (
    AdvancedTestInputs,
    DeadtimePoint,
    calculate_advanced_test,
    cc_per_min_from_lb_per_hour,
    default_deadtime_curve,
    lb_per_hour_from_cc_per_min,
)


class AdvancedTestingCalculationTests(unittest.TestCase):
    def test_conversion_helpers_round_trip(self) -> None:
        cc_per_min = cc_per_min_from_lb_per_hour(42.0)
        self.assertAlmostEqual(42.0, lb_per_hour_from_cc_per_min(cc_per_min))

    def test_nominal_4_stroke_calculation(self) -> None:
        result = calculate_advanced_test(
            AdvancedTestInputs(
                battery_voltage=13.8,
                desired_fuel_ml=5.0,
                injector_size_cc_per_min=336.0,
                rpm=1200.0,
                duration_seconds=30.0,
                deadtime_curve=default_deadtime_curve(),
            )
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(300, result.pulse_count)
        self.assertAlmostEqual(0.792, result.interpolated_deadtime_ms, places=3)
        self.assertAlmostEqual(2.976190476, result.effective_open_time_ms, places=6)
        self.assertAlmostEqual(3.768190476, result.commanded_pulse_width_ms, places=6)
        self.assertAlmostEqual(3.768190476, result.duty_cycle_percent, places=6)
        self.assertEqual((), result.errors)

    def test_voltage_outside_curve_is_clamped_with_warning(self) -> None:
        result = calculate_advanced_test(
            AdvancedTestInputs(
                battery_voltage=18.0,
                desired_fuel_ml=3.0,
                injector_size_cc_per_min=300.0,
                rpm=1000.0,
                duration_seconds=30.0,
                deadtime_curve=default_deadtime_curve(),
            )
        )

        self.assertTrue(result.is_valid)
        self.assertTrue(result.voltage_was_clamped)
        self.assertEqual(16.0, result.applied_voltage)
        self.assertTrue(any("clamped" in warning.lower() for warning in result.warnings))

    def test_duty_cycle_at_or_above_one_hundred_percent_blocks(self) -> None:
        result = calculate_advanced_test(
            AdvancedTestInputs(
                battery_voltage=14.0,
                desired_fuel_ml=250.0,
                injector_size_cc_per_min=80.0,
                rpm=6000.0,
                duration_seconds=10.0,
                deadtime_curve=(
                    DeadtimePoint(voltage=12.0, deadtime_ms=0.9),
                    DeadtimePoint(voltage=14.0, deadtime_ms=0.8),
                ),
            )
        )

        self.assertFalse(result.is_valid)
        self.assertGreaterEqual(result.duty_cycle_percent, 100.0)
        self.assertTrue(any("100%" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()

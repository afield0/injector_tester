from __future__ import annotations

import unittest

from desktop_app.advanced_testing import DeadtimePoint
from desktop_app.injector_profile import (
    PROFILE_FORMAT_NAME,
    InjectorProfile,
    dump_injector_profile,
    load_injector_profile,
)


class InjectorProfileTests(unittest.TestCase):
    def test_dump_and_load_round_trip(self) -> None:
        profile = InjectorProfile(
            injector_lb_per_hour=32.0,
            injector_cc_per_min=336.0,
            deadtime_curve=(
                DeadtimePoint(voltage=8.0, deadtime_ms=1.3),
                DeadtimePoint(voltage=14.0, deadtime_ms=0.78),
            ),
        )

        loaded = load_injector_profile(dump_injector_profile(profile))

        self.assertEqual(profile, loaded)

    def test_loader_accepts_comments_and_blank_lines(self) -> None:
        loaded = load_injector_profile(
            f"""
            # Bosch EV14 example
            format={PROFILE_FORMAT_NAME}
            injector_lb_per_hour=42.00
            injector_cc_per_min=441.00

            deadtime=8.00,1.250
            deadtime=14.00,0.800
            """
        )

        self.assertEqual(42.0, loaded.injector_lb_per_hour)
        self.assertEqual(441.0, loaded.injector_cc_per_min)
        self.assertEqual(2, len(loaded.deadtime_curve))

    def test_loader_rejects_missing_deadtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one deadtime entry"):
            load_injector_profile(
                f"format={PROFILE_FORMAT_NAME}\n"
                "injector_lb_per_hour=32.00\n"
                "injector_cc_per_min=336.00\n"
            )


if __name__ == "__main__":
    unittest.main()

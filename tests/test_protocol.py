from __future__ import annotations

import unittest

from desktop_app.protocol import HelpResponse, ReadyResponse, ResponseParser, VersionResponse


class ResponseParserTests(unittest.TestCase):
    def test_version_response_parses(self) -> None:
        parser = ResponseParser()

        responses = parser.feed_line("VERSION 1.1.0")

        self.assertEqual([VersionResponse("1.1.0")], responses)

    def test_startup_banner_parses_help_lines_for_unknown_commands(self) -> None:
        parser = ResponseParser()
        lines = (
            "Injector mask-ISR controller ready",
            "Commands:",
            "  HELP",
            "  STATUS",
            "  VERSION",
            "  MODEL <0|1>",
            "  SET <channel 1-4> <rpm> <dutyPercent>",
            "  SETMASK <mask 1-15> <rpm> <dutyPercent>",
            "",
            "Grouped mask semantics:",
            "  STARTMASK and RUNMASK initialize selected outputs from the",
            "  inactive phase and apply timing state together in one path.",
            "",
            "Models:",
            "  0 = 4-stroke, 1 event per 2 revs (Hz = RPM/120)",
            "  1 = 1 event per rev            (Hz = RPM/60)",
            "VERSION 1.1.0",
        )

        responses = []
        for line in lines:
            responses.extend(parser.feed_line(line))

        self.assertEqual(3, len(responses))
        self.assertIsInstance(responses[0], ReadyResponse)
        self.assertIsInstance(responses[1], HelpResponse)
        self.assertEqual(VersionResponse("1.1.0"), responses[2])
        self.assertEqual(
            (
                "Commands:",
                "HELP",
                "STATUS",
                "VERSION",
                "MODEL <0|1>",
                "SET <channel 1-4> <rpm> <dutyPercent>",
                "SETMASK <mask 1-15> <rpm> <dutyPercent>",
                "",
                "Grouped mask semantics:",
                "STARTMASK and RUNMASK initialize selected outputs from the",
                "inactive phase and apply timing state together in one path.",
                "",
                "Models:",
                "0 = 4-stroke, 1 event per 2 revs (Hz = RPM/120)",
                "1 = 1 event per rev            (Hz = RPM/60)",
            ),
            responses[1].lines,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

try:
    from PySide6.QtCore import QObject, Signal
    from desktop_app.protocol import StatusResponse, VersionResponse
    from desktop_app.state import AppController

    HAVE_PYSIDE6 = True
except ModuleNotFoundError:  # pragma: no cover - depends on local test environment
    HAVE_PYSIDE6 = False


if HAVE_PYSIDE6:
    class FakeTransport(QObject):
        connection_changed = Signal(bool, str, str)
        raw_line_received = Signal(str)
        acknowledgement_received = Signal(object)
        error_received = Signal(object)
        status_received = Signal(object)
        help_received = Signal(object)
        ready_received = Signal(object)
        version_received = Signal(object)

        def __init__(self) -> None:
            super().__init__()
            self.sent_lines: list[str] = []

        def open(self, config: object) -> None:
            port = getattr(config, "port")
            self.connection_changed.emit(True, port, "fake")

        def close(self) -> None:
            self.connection_changed.emit(False, "", "fake")

        def send_line(self, line: str) -> None:
            self.sent_lines.append(line.rstrip("\n"))

        def enumerate_ports(self) -> tuple[object, ...]:
            return ()


@unittest.skipUnless(HAVE_PYSIDE6, "PySide6 is not installed")
class AppControllerVerificationTests(unittest.TestCase):
    def test_connect_waits_for_ready_before_initial_probe(self) -> None:
        transport = FakeTransport()
        controller = AppController(transport)

        controller.connect_port("/dev/ttyFAKE0")

        self.assertEqual([], transport.sent_lines)
        self.assertFalse(controller.state.connection_verified)
        self.assertEqual(
            "Connection opened. Waiting for controller startup...",
            controller.state.verification_message,
        )

        transport.ready_received.emit(object())

        self.assertEqual(["VERSION", "STATUS"], transport.sent_lines)
        self.assertEqual("Verification in progress...", controller.state.verification_message)

        transport.version_received.emit(VersionResponse("1.1.0"))
        self.assertEqual("1.1.0", controller.state.firmware_version)
        self.assertFalse(controller.state.connection_verified)

        transport.status_received.emit(
            StatusResponse(
                model=0,
                tick_us=20,
                active_mask=0,
                state_mask=0,
                channels=(),
            )
        )

        self.assertTrue(controller.state.connection_verified)
        self.assertEqual(
            "Connection verified. Firmware version 1.1.0.",
            controller.state.verification_message,
        )

    def test_version_mismatch_blocks_verification(self) -> None:
        transport = FakeTransport()
        controller = AppController(transport)

        controller.connect_port("/dev/ttyFAKE0")
        transport.ready_received.emit(object())
        transport.version_received.emit(VersionResponse("1.0.0"))

        self.assertFalse(controller.state.connection_verified)
        self.assertIn("firmware version mismatch", controller.state.verification_message.lower())


if __name__ == "__main__":
    unittest.main()

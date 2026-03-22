from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .state import AppController
from .transport import SerialManager
from .ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    transport = SerialManager()
    controller = AppController(transport)
    window = MainWindow(controller)
    window.show()

    exit_code = app.exec()
    transport.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

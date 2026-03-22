from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from .protocol import ErrorResponse, HelpResponse, OkResponse, ReadyResponse, ResponseParser, StatusResponse

try:
    from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo
except ImportError:  # pragma: no cover - depends on local PySide6 build
    QSerialPort = None
    QSerialPortInfo = None

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - dependency may be absent during static review
    serial = None
    list_ports = None

    class SerialException(Exception):
        pass


@dataclass(frozen=True)
class SerialPortDescriptor:
    port_name: str
    system_location: str
    description: str = ""
    manufacturer: str = ""


@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int = 115200
    timeout: float = 0.1


class _PySerialWorker(QObject):
    connected = Signal(str)
    disconnected = Signal()
    backend_error = Signal(str)
    raw_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._serial = None
        self._buffer = bytearray()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(10)
        self._poll_timer.timeout.connect(self._poll_serial)

    @Slot(object)
    def open_port(self, config: SerialConfig) -> None:
        if serial is None:
            self.backend_error.emit("pyserial is not installed")
            return

        self.close_port()
        try:
            self._serial = serial.Serial(config.port, config.baudrate, timeout=config.timeout)
        except SerialException as exc:
            self.backend_error.emit(str(exc))
            return

        self.connected.emit(config.port)
        self._poll_timer.start()

    @Slot(str)
    def send_line(self, line: str) -> None:
        if self._serial is None:
            self.backend_error.emit("serial port is not open")
            return

        try:
            self._serial.write(line.encode("utf-8"))
            self._serial.flush()
        except SerialException as exc:
            self.backend_error.emit(str(exc))
            self.close_port()

    @Slot()
    def close_port(self) -> None:
        was_open = self._serial is not None
        self._poll_timer.stop()
        self._buffer.clear()
        if self._serial is not None:
            try:
                self._serial.close()
            except SerialException:
                pass
            finally:
                self._serial = None
        if was_open:
            self.disconnected.emit()

    @Slot()
    def _poll_serial(self) -> None:
        if self._serial is None:
            return

        try:
            waiting = self._serial.in_waiting
        except SerialException as exc:
            self.backend_error.emit(str(exc))
            self.close_port()
            return

        if waiting <= 0:
            return

        try:
            chunk = self._serial.read(waiting)
        except SerialException as exc:
            self.backend_error.emit(str(exc))
            self.close_port()
            return

        if chunk:
            self._buffer.extend(chunk)
            self._drain_lines()

    def _drain_lines(self) -> None:
        while True:
            newline_index = self._buffer.find(b"\n")
            if newline_index < 0:
                return
            raw = bytes(self._buffer[:newline_index])
            del self._buffer[: newline_index + 1]
            self.raw_line.emit(raw.rstrip(b"\r").decode("utf-8", errors="replace"))


class SerialManager(QObject):
    connection_changed = Signal(bool, str, str)
    raw_line_received = Signal(str)
    acknowledgement_received = Signal(object)
    error_received = Signal(object)
    status_received = Signal(object)
    help_received = Signal(object)
    ready_received = Signal(object)

    _fallback_open_requested = Signal(object)
    _fallback_send_requested = Signal(str)
    _fallback_close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._parser = ResponseParser()
        self._backend_name = "qtserialport" if QSerialPort is not None else "pyserial"
        self._connected_port = ""
        self._qt_serial: QSerialPort | None = None
        self._qt_buffer = bytearray()
        self._thread: QThread | None = None
        self._worker: _PySerialWorker | None = None

        if QSerialPort is not None:
            self._init_qt_backend()
        else:
            self._init_fallback_backend()

    def enumerate_ports(self) -> tuple[SerialPortDescriptor, ...]:
        if QSerialPortInfo is not None:
            ports = []
            for info in QSerialPortInfo.availablePorts():
                ports.append(
                    SerialPortDescriptor(
                        port_name=info.portName(),
                        system_location=info.systemLocation(),
                        description=info.description(),
                        manufacturer=info.manufacturer(),
                    )
                )
            return tuple(ports)

        if list_ports is None:
            return ()

        ports = []
        for info in list_ports.comports():
            ports.append(
                SerialPortDescriptor(
                    port_name=info.name,
                    system_location=info.device,
                    description=info.description or "",
                    manufacturer=info.manufacturer or "",
                )
            )
        return tuple(ports)

    def backend_name(self) -> str:
        return self._backend_name

    def open(self, config: SerialConfig) -> None:
        self._reset_parser()
        if QSerialPort is not None:
            self._open_qt_serial(config)
            return
        self._fallback_open_requested.emit(config)

    def send_line(self, line: str) -> None:
        payload = line if line.endswith("\n") else f"{line}\n"
        if QSerialPort is not None:
            if self._qt_serial is None or not self._qt_serial.isOpen():
                self._emit_backend_error("serial port is not open")
                return
            written = self._qt_serial.write(payload.encode("utf-8"))
            if written < 0:
                self._emit_backend_error(self._qt_serial.errorString())
            return
        self._fallback_send_requested.emit(payload)

    def close(self) -> None:
        self._reset_parser()
        if QSerialPort is not None:
            self._close_qt_serial()
            return
        self._fallback_close_requested.emit()

    def shutdown(self) -> None:
        self.close()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1000)

    def _init_qt_backend(self) -> None:
        self._qt_serial = QSerialPort(self)
        self._qt_serial.readyRead.connect(self._on_qt_ready_read)
        self._qt_serial.errorOccurred.connect(self._on_qt_error)

    def _init_fallback_backend(self) -> None:
        self._thread = QThread()
        self._worker = _PySerialWorker()
        self._worker.moveToThread(self._thread)
        self._thread.start()

        self._fallback_open_requested.connect(self._worker.open_port)
        self._fallback_send_requested.connect(self._worker.send_line)
        self._fallback_close_requested.connect(self._worker.close_port)

        self._worker.connected.connect(self._on_backend_connected)
        self._worker.disconnected.connect(self._on_backend_disconnected)
        self._worker.backend_error.connect(self._emit_backend_error)
        self._worker.raw_line.connect(self._handle_raw_line)

    def _open_qt_serial(self, config: SerialConfig) -> None:
        assert self._qt_serial is not None
        if self._qt_serial.isOpen():
            self._close_qt_serial()

        self._qt_buffer.clear()
        self._qt_serial.setPortName(config.port)
        self._qt_serial.setBaudRate(config.baudrate)
        self._qt_serial.setDataBits(QSerialPort.Data8)
        self._qt_serial.setParity(QSerialPort.NoParity)
        self._qt_serial.setStopBits(QSerialPort.OneStop)
        self._qt_serial.setFlowControl(QSerialPort.NoFlowControl)

        if self._qt_serial.open(QSerialPort.ReadWrite):
            self._on_backend_connected(config.port)
            return

        self._emit_backend_error(self._qt_serial.errorString())

    def _close_qt_serial(self) -> None:
        assert self._qt_serial is not None
        was_open = self._qt_serial.isOpen()
        self._qt_buffer.clear()
        self._qt_serial.close()
        if was_open:
            self._on_backend_disconnected()

    @Slot()
    def _on_qt_ready_read(self) -> None:
        assert self._qt_serial is not None
        self._qt_buffer.extend(bytes(self._qt_serial.readAll()))

        while True:
            newline_index = self._qt_buffer.find(b"\n")
            if newline_index < 0:
                return
            raw = bytes(self._qt_buffer[:newline_index])
            del self._qt_buffer[: newline_index + 1]
            self._handle_raw_line(raw.rstrip(b"\r").decode("utf-8", errors="replace"))

    @Slot(object)
    def _on_qt_error(self, error: object) -> None:
        if QSerialPort is None or self._qt_serial is None:
            return
        if error == QSerialPort.NoError:
            return
        self._emit_backend_error(self._qt_serial.errorString())
        if not self._qt_serial.isOpen():
            self._on_backend_disconnected()

    @Slot(str)
    def _on_backend_connected(self, port: str) -> None:
        self._reset_parser()
        self._connected_port = port
        self.connection_changed.emit(True, port, self._backend_name)

    @Slot()
    def _on_backend_disconnected(self) -> None:
        port = self._connected_port
        self._connected_port = ""
        self._reset_parser()
        self.connection_changed.emit(False, port, self._backend_name)

    @Slot(str)
    def _emit_backend_error(self, message: str) -> None:
        self.error_received.emit(ErrorResponse(message))

    def _reset_parser(self) -> None:
        self._parser = ResponseParser()

    @Slot(str)
    def _handle_raw_line(self, line: str) -> None:
        self.raw_line_received.emit(line)
        for response in self._parser.feed_line(line):
            if isinstance(response, OkResponse):
                self.acknowledgement_received.emit(response)
            elif isinstance(response, StatusResponse):
                self.status_received.emit(response)
            elif isinstance(response, ErrorResponse):
                self.error_received.emit(response)
            elif isinstance(response, HelpResponse):
                self.help_received.emit(response)
            elif isinstance(response, ReadyResponse):
                self.ready_received.emit(response)

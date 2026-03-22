from __future__ import annotations

from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, Signal, Slot

from .protocol import (
    CHANNEL_MASK_ALL,
    ChannelStatus,
    Command,
    CommandName,
    ErrorResponse,
    HelpResponse,
    OkResponse,
    ReadyResponse,
    StatusResponse,
    channel_to_mask,
    help_command,
    mask_to_channels,
    model_command,
    run_command,
    set_command,
    start_command,
    startall_command,
    status_command,
    stop_command,
    stopall_command,
)
from .transport import SerialConfig, SerialManager


@dataclass(frozen=True)
class ChannelConfig:
    channel: int
    rpm: float = 1000.0
    duty: float = 25.0
    enabled: bool = False
    state: bool = False
    mode: str = "CONT"
    on_ticks: int = 0
    off_ticks: int = 0
    ticks_left: int = 0
    pulses_left: int = 0
    stop_after_low: bool = False


@dataclass(frozen=True)
class FirmwareStatus:
    model: int = 0
    tick_us: int = 20
    active_mask: int = 0
    state_mask: int = 0


@dataclass(frozen=True)
class AppState:
    safety_warning: str = (
        "Safety warning: do not connect injectors directly to Arduino pins. "
        "Use proper low-side drivers or MOSFETs with flyback protection, an external injector supply, "
        "and a shared ground."
    )
    selected_action_mode_label: str = (
        "Operator meaning for grouped selected-channel commands: Start Selected and "
        "Run Selected initialize all selected outputs from the inactive phase and "
        "apply timing state together within one command handling path."
    )
    connection_port: str | None = None
    connected: bool = False
    firmware_status: FirmwareStatus = field(default_factory=FirmwareStatus)
    selected_mask: int = 1
    status_message: str = "Disconnected"
    last_error_message: str = ""
    help_text: str = ""
    log_lines: tuple[str, ...] = ()
    channels: tuple[ChannelConfig, ...] = field(
        default_factory=lambda: tuple(ChannelConfig(channel=index + 1) for index in range(4))
    )

    @property
    def selected_channels(self) -> tuple[int, ...]:
        return mask_to_channels(self.selected_mask)

    @property
    def pulse_model(self) -> int:
        return self.firmware_status.model

    @property
    def tick_us(self) -> int:
        return self.firmware_status.tick_us

    @property
    def active_mask(self) -> int:
        return self.firmware_status.active_mask

    @property
    def state_mask(self) -> int:
        return self.firmware_status.state_mask

    @property
    def has_error(self) -> bool:
        return bool(self.last_error_message)


class AppController(QObject):
    state_changed = Signal(object)
    log_message = Signal(str)

    def __init__(self, transport: SerialManager) -> None:
        super().__init__()
        self._transport = transport
        self._state = AppState()

        transport.connection_changed.connect(self._on_connection_changed)
        transport.raw_line_received.connect(self._on_raw_line)
        transport.ready_received.connect(self._on_ready)
        transport.help_received.connect(self._on_help)
        transport.acknowledgement_received.connect(self._on_ack)
        transport.error_received.connect(self._on_error)
        transport.status_received.connect(self._on_status)

    @property
    def state(self) -> AppState:
        return self._state

    def _set_state(self, new_state: AppState) -> None:
        self._state = new_state
        self.state_changed.emit(new_state)

    def _append_log(self, line: str) -> None:
        lines = (*self._state.log_lines[-199:], line)
        self._set_state(replace(self._state, log_lines=lines))
        self.log_message.emit(line)

    def _set_error(self, message: str) -> None:
        self._append_log(f"Error: {message}")
        self._set_state(replace(self._state, status_message=message, last_error_message=message))

    def _clear_error(self, status_message: str | None = None) -> None:
        new_status = self._state.status_message if status_message is None else status_message
        self._set_state(replace(self._state, status_message=new_status, last_error_message=""))

    @staticmethod
    def _validate_rpm(rpm: float) -> str | None:
        if not (1.0 <= rpm <= 50000.0):
            return "RPM must be between 1.0 and 50000.0"
        return None

    @staticmethod
    def _validate_duty(duty: float) -> str | None:
        if not (0.0 < duty < 100.0):
            return "Duty must be greater than 0 and less than 100"
        return None

    @staticmethod
    def _validate_pulses(pulses: int) -> str | None:
        if pulses < 1:
            return "Pulse count must be a positive integer"
        return None

    @Slot(bool, str, str)
    def _on_connection_changed(self, connected: bool, port: str, backend: str) -> None:
        if connected:
            self._set_state(
                replace(
                    self._state,
                    connected=True,
                    connection_port=port,
                    status_message=f"Connected: {port} via {backend}",
                    last_error_message="",
                )
            )
            self.send_command(help_command())
            self.refresh_status()
            return

        self._set_state(
            replace(
                self._state,
                connected=False,
                connection_port=None,
                status_message="Disconnected",
                last_error_message="",
            )
        )

    @Slot(str)
    def _on_raw_line(self, line: str) -> None:
        self._append_log(f"<< {line}")

    @Slot(object)
    def _on_ready(self, response: ReadyResponse) -> None:
        self._clear_error(response.message)

    @Slot(object)
    def _on_help(self, response: HelpResponse) -> None:
        self._set_state(replace(self._state, help_text="\n".join(response.lines)))

    @Slot(object)
    def _on_error(self, response: ErrorResponse) -> None:
        self._set_error(f"ERR: {response.message}")

    @Slot(object)
    def _on_ack(self, response: OkResponse) -> None:
        self._clear_error(self._format_ok(response))
        if response.command in {
            CommandName.MODEL,
            CommandName.SET,
            CommandName.START,
            CommandName.RUN,
            CommandName.STOP,
            CommandName.STARTALL,
            CommandName.STOPALL,
        }:
            self.refresh_status()

    @Slot(object)
    def _on_status(self, response: StatusResponse) -> None:
        firmware_status = FirmwareStatus(
            model=response.model,
            tick_us=response.tick_us,
            active_mask=response.active_mask,
            state_mask=response.state_mask,
        )
        channels = tuple(self._channel_from_status(channel) for channel in response.channels)
        self._set_state(
            replace(
                self._state,
                firmware_status=firmware_status,
                channels=channels,
                status_message="Status updated",
                last_error_message="",
            )
        )

    @staticmethod
    def _channel_from_status(status: ChannelStatus) -> ChannelConfig:
        return ChannelConfig(
            channel=status.channel,
            rpm=status.rpm,
            duty=status.duty,
            enabled=status.enabled,
            state=status.state,
            mode=status.mode,
            on_ticks=status.on_ticks,
            off_ticks=status.off_ticks,
            ticks_left=status.ticks_left,
            pulses_left=status.pulses_left,
            stop_after_low=status.stop_after_low,
        )

    @staticmethod
    def _format_ok(response: OkResponse) -> str:
        detail = " ".join(response.detail).strip()
        return f"{response.command.value} OK" + (f": {detail}" if detail else "")

    def connect_port(self, port: str, baudrate: int = 115200) -> None:
        self._transport.open(SerialConfig(port=port, baudrate=baudrate))

    def list_ports(self) -> tuple[str, ...]:
        return tuple(port.system_location for port in self._transport.enumerate_ports())

    def disconnect_port(self) -> None:
        self._transport.close()

    def send_command(self, command: Command) -> None:
        self._append_log(f">> {command.encode()}")
        self._transport.send_line(command.encode())

    def set_selected_channels(self, channels: list[int]) -> None:
        mask = 0
        for channel in channels:
            mask |= channel_to_mask(channel)
        self._set_state(replace(self._state, selected_mask=mask & CHANNEL_MASK_ALL))

    def set_model(self, model: int) -> None:
        self.send_command(model_command(model))

    def refresh_status(self) -> None:
        self.send_command(status_command())

    def request_help(self) -> None:
        self.send_command(help_command())

    def report_validation_error(self, message: str) -> None:
        self._set_error(message)

    def apply_channel_settings(self, rpm: float, duty: float) -> None:
        rpm_error = self._validate_rpm(rpm)
        if rpm_error is not None:
            self._set_error(rpm_error)
            return
        duty_error = self._validate_duty(duty)
        if duty_error is not None:
            self._set_error(duty_error)
            return
        self._append_log(
            "Selected-action compatibility fallback: expanding Apply Config into per-channel SET commands."
        )
        for channel in self._state.selected_channels:
            self.send_command(set_command(channel, rpm, duty))

    def start_selected(self) -> None:
        self._append_log(
            "Selected-action compatibility fallback: expanding Start Selected into per-channel START commands."
        )
        for channel in self._state.selected_channels:
            self.send_command(start_command(channel))

    def start_all(self) -> None:
        self.send_command(startall_command())

    def stop_selected(self) -> None:
        self._append_log(
            "Selected-action compatibility fallback: expanding Stop Selected into per-channel STOP commands."
        )
        for channel in self._state.selected_channels:
            self.send_command(stop_command(channel))

    def stop_all(self) -> None:
        self.send_command(stopall_command())

    def run_selected(self, pulses: int) -> None:
        pulses_error = self._validate_pulses(pulses)
        if pulses_error is not None:
            self._set_error(pulses_error)
            return
        self._append_log(
            "Selected-action compatibility fallback: expanding Run Selected into per-channel RUN commands."
        )
        for channel in self._state.selected_channels:
            self.send_command(run_command(channel, pulses))

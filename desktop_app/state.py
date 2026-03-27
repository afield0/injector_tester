from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .protocol import (
    CHANNEL_MASK_ALL,
    EXPECTED_FIRMWARE_VERSION,
    ChannelStatus,
    Command,
    CommandName,
    ErrorResponse,
    HelpResponse,
    OkResponse,
    ReadyResponse,
    StatusResponse,
    VersionResponse,
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
    version_command,
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
class TestProgress:
    active: bool = False
    mode: str = "idle"
    value: int = 0
    minimum: int = 0
    maximum: int = 100
    label: str = "Idle"


@dataclass(frozen=True)
class AppState:
    safety_warning: str = (
        "Safety warning: do not connect injectors directly to Arduino pins. "
        "Use proper low-side drivers or MOSFETs with flyback protection, an external injector supply, "
        "and a shared ground."
    )
    selected_action_mode_label: str = (
        "All mode applies the current configuration to every checked channel and runs them "
        "together for the requested pulse count. Sequential mode runs checked channels one at a time."
    )
    connection_port: str | None = None
    connected: bool = False
    connection_verified: bool = False
    verification_message: str = "Connection not verified"
    firmware_version: str = "Unknown"
    expected_firmware_version: str = EXPECTED_FIRMWARE_VERSION
    test_mode: str = "sequential"
    wizard_step: int = 0
    selected_test_kind: Literal["simple", "advanced"] | None = None
    firmware_status: FirmwareStatus = field(default_factory=FirmwareStatus)
    test_progress: TestProgress = field(default_factory=TestProgress)
    auto_poll_enabled: bool = True
    auto_poll_interval_ms: int = 1000
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

    @property
    def can_navigate_back(self) -> bool:
        return not self.test_progress.active

    @property
    def wizard_test_kind(self) -> Literal["simple", "advanced"] | None:
        return self.selected_test_kind


class AppController(QObject):
    state_changed = Signal(object)
    log_message = Signal(str)

    def __init__(self, transport: SerialManager) -> None:
        super().__init__()
        self._transport = transport
        self._state = AppState()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.refresh_status)
        self._tracked_test_mask = 0
        self._tracked_total_pulses = 0
        self._tracked_total_channels = 0
        self._tracked_mode = "idle"
        self._tracked_execution_mode = "all"
        self._sequential_pending_channels: tuple[int, ...] = ()
        self._sequential_current_channel: int | None = None
        self._sequential_completed_channels = 0
        self._sequential_current_started = False
        self._pending_verification = False
        self._pending_version_match = False
        self._pending_status_probe = False

        transport.connection_changed.connect(self._on_connection_changed)
        transport.raw_line_received.connect(self._on_raw_line)
        transport.ready_received.connect(self._on_ready)
        transport.help_received.connect(self._on_help)
        transport.version_received.connect(self._on_version)
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

    def _set_status_message(self, message: str) -> None:
        self._set_state(replace(self._state, status_message=message))

    def _reset_verification_tracking(self) -> None:
        self._pending_verification = False
        self._pending_version_match = False
        self._pending_status_probe = False

    def _begin_connection_verification(self, message: str) -> None:
        self._pending_verification = True
        self._pending_version_match = False
        self._pending_status_probe = False
        self._set_state(
            replace(
                self._state,
                connection_verified=False,
                verification_message=message,
            )
        )
        self.send_command(version_command())
        self.send_command(status_command())

    def _complete_verification_if_ready(self) -> None:
        if not self._pending_verification:
            return
        if self._pending_version_match and self._pending_status_probe:
            version = self._state.firmware_version
            self._reset_verification_tracking()
            self._set_state(
                replace(
                    self._state,
                    connection_verified=True,
                    verification_message=f"Connection verified. Firmware version {version}.",
                )
            )

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

    def _update_poll_timer(self) -> None:
        should_poll = self._state.auto_poll_enabled or self._is_sequential_active()
        if should_poll and self._state.test_progress.active:
            self._poll_timer.start(self._state.auto_poll_interval_ms)
            return
        self._poll_timer.stop()

    def _set_test_progress(self, progress: TestProgress) -> None:
        self._set_state(replace(self._state, test_progress=progress))
        self._update_poll_timer()

    def _is_sequential_active(self) -> bool:
        return self._sequential_current_channel is not None or bool(self._sequential_pending_channels)

    @staticmethod
    def _action_mode_label(test_mode: str) -> str:
        if test_mode == "sequential":
            return (
                "Sequential mode runs the checked channels one at a time for the requested pulse count."
            )
        return (
            "All mode applies the current configuration to every checked channel and runs them "
            "together for the requested pulse count."
        )

    def _begin_test_tracking(self, mode: str, pulses: int = 0, execution_mode: str = "all") -> None:
        self._tracked_test_mask = self._state.selected_mask
        self._tracked_total_pulses = pulses
        self._tracked_total_channels = len(self._state.selected_channels)
        self._tracked_mode = mode
        self._tracked_execution_mode = execution_mode

        if mode == "counted":
            progress = TestProgress(
                active=True,
                mode=mode,
                value=0,
                minimum=0,
                maximum=100,
                label=(
                    "Sequential counted run queued"
                    if execution_mode == "sequential"
                    else f"Counted run in progress: 0% ({pulses} pulses/channel)"
                ),
            )
        else:
            progress = TestProgress(
                active=True,
                mode=mode,
                value=0,
                minimum=0,
                maximum=0,
                label="Continuous run active",
            )

        self._set_test_progress(progress)

    def _clear_test_tracking(self, label: str = "Idle") -> None:
        self._tracked_test_mask = 0
        self._tracked_total_pulses = 0
        self._tracked_total_channels = 0
        self._tracked_mode = "idle"
        self._tracked_execution_mode = "all"
        self._sequential_pending_channels = ()
        self._sequential_current_channel = None
        self._sequential_completed_channels = 0
        self._sequential_current_started = False
        self._set_test_progress(TestProgress(label=label))

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
                    connection_verified=False,
                    verification_message="Connection opened. Verifying controller...",
                    firmware_version="Unknown",
                )
            )
            self.send_command(help_command())
            self._begin_connection_verification("Verification in progress...")
            return

        self._reset_verification_tracking()
        self._clear_test_tracking()
        self._set_state(
            replace(
                self._state,
                connected=False,
                connection_port=None,
                status_message="Disconnected",
                last_error_message="",
                connection_verified=False,
                verification_message="Connection not verified",
                firmware_version="Unknown",
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
    def _on_version(self, response: VersionResponse) -> None:
        verification_message = self._state.verification_message
        connection_verified = self._state.connection_verified

        if self._pending_verification:
            if response.version != self._state.expected_firmware_version:
                self._reset_verification_tracking()
                connection_verified = False
                verification_message = (
                    "Verification failed: firmware version mismatch "
                    f"(expected {self._state.expected_firmware_version}, got {response.version})."
                )
            else:
                self._pending_version_match = True
                if not self._pending_status_probe:
                    verification_message = (
                        f"Firmware version {response.version} accepted. Waiting for STATUS response..."
                    )

        self._set_state(
            replace(
                self._state,
                firmware_version=response.version,
                connection_verified=connection_verified,
                verification_message=verification_message,
            )
        )
        self._complete_verification_if_ready()

    @Slot(object)
    def _on_error(self, response: ErrorResponse) -> None:
        if self._pending_verification:
            self._reset_verification_tracking()
            self._set_state(
                replace(
                    self._state,
                    connection_verified=False,
                    verification_message="Verification failed due to device error response.",
                )
            )
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
        progress = self._derive_test_progress(channels)
        verification_message = self._state.verification_message
        connection_verified = self._state.connection_verified
        if self._pending_verification:
            self._pending_status_probe = True
            if self._pending_version_match:
                verification_message = (
                    f"Firmware version {self._state.firmware_version} accepted. Completing verification..."
                )
            else:
                verification_message = "STATUS received. Waiting for firmware version response..."

        self._set_state(
            replace(
                self._state,
                firmware_status=firmware_status,
                test_progress=progress,
                channels=channels,
                status_message="Status updated",
                last_error_message="",
                connection_verified=connection_verified,
                verification_message=verification_message,
            )
        )
        self._complete_verification_if_ready()
        self._update_poll_timer()
        self._advance_sequential_counted_run(channels)

    def _derive_test_progress(self, channels: tuple[ChannelConfig, ...]) -> TestProgress:
        if self._tracked_test_mask == 0 or self._tracked_mode == "idle":
            return self._state.test_progress

        tracked = [channel for channel in channels if self._tracked_test_mask & channel_to_mask(channel.channel)]
        if not tracked:
            return self._state.test_progress

        any_enabled = any(channel.enabled for channel in tracked)

        if self._tracked_mode == "counted" and self._tracked_total_pulses > 0:
            if self._tracked_execution_mode == "sequential":
                total = self._tracked_total_pulses * self._tracked_total_channels
                completed = self._sequential_completed_channels * self._tracked_total_pulses
                if self._sequential_current_channel is not None:
                    current = next(
                        (channel for channel in channels if channel.channel == self._sequential_current_channel),
                        None,
                    )
                    if current is not None:
                        if current.enabled or current.pulses_left > 0:
                            self._sequential_current_started = True
                        if self._sequential_current_started:
                            completed += max(0, self._tracked_total_pulses - current.pulses_left)
                percent = int((completed * 100) / total) if total > 0 else 0
                if (
                    self._sequential_completed_channels >= self._tracked_total_channels
                    and self._sequential_current_channel is None
                    and not self._sequential_pending_channels
                ):
                    self._tracked_test_mask = 0
                    self._tracked_total_pulses = 0
                    self._tracked_total_channels = 0
                    self._tracked_mode = "idle"
                    self._tracked_execution_mode = "all"
                    return TestProgress(value=100, label="Sequential counted run complete")
                current_label = (
                    f"CH{self._sequential_current_channel}"
                    if self._sequential_current_channel is not None
                    else "waiting"
                )
                return TestProgress(
                    active=True,
                    mode="counted",
                    value=max(0, min(100, percent)),
                    minimum=0,
                    maximum=100,
                    label=f"Sequential counted run: {percent}% ({current_label})",
                )

            remaining = sum(channel.pulses_left for channel in tracked)
            total = self._tracked_total_pulses * len(tracked)
            completed = max(0, total - remaining)
            percent = int((completed * 100) / total) if total > 0 else 0
            if not any_enabled and remaining == 0:
                self._tracked_test_mask = 0
                self._tracked_total_pulses = 0
                self._tracked_total_channels = 0
                self._tracked_mode = "idle"
                self._tracked_execution_mode = "all"
                return TestProgress(value=100, label="Counted run complete")
            return TestProgress(
                active=True,
                mode="counted",
                value=max(0, min(100, percent)),
                minimum=0,
                maximum=100,
                label=f"Counted run in progress: {percent}%",
            )

        if any_enabled:
            return TestProgress(
                active=True,
                mode="continuous",
                value=0,
                minimum=0,
                maximum=0,
                label="Continuous run active",
            )

        self._tracked_test_mask = 0
        self._tracked_total_pulses = 0
        self._tracked_total_channels = 0
        self._tracked_mode = "idle"
        self._tracked_execution_mode = "all"
        return TestProgress(label="Idle")

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

    def _start_next_sequential_counted_channel(self) -> None:
        if not self._sequential_pending_channels:
            self._sequential_current_channel = None
            self.refresh_status()
            return

        next_channel = self._sequential_pending_channels[0]
        self._sequential_pending_channels = self._sequential_pending_channels[1:]
        self._sequential_current_channel = next_channel
        self._sequential_current_started = False
        self._append_log(f"Sequential mode: starting counted test on CH{next_channel}.")
        self.send_command(run_command(next_channel, self._tracked_total_pulses))

    def _advance_sequential_counted_run(self, channels: tuple[ChannelConfig, ...]) -> None:
        if self._tracked_execution_mode != "sequential" or self._tracked_mode != "counted":
            return
        if self._sequential_current_channel is None:
            return

        current = next(
            (channel for channel in channels if channel.channel == self._sequential_current_channel),
            None,
        )
        if current is None:
            return
        if current.enabled or current.pulses_left > 0:
            self._sequential_current_started = True
            return
        if not self._sequential_current_started:
            return

        completed_channel = self._sequential_current_channel
        self._append_log(f"Sequential mode: CH{completed_channel} counted test complete.")
        self._sequential_current_channel = None
        self._sequential_current_started = False
        self._sequential_completed_channels += 1
        self._start_next_sequential_counted_channel()

    def connect_port(self, port: str, baudrate: int = 115200) -> None:
        self._reset_verification_tracking()
        self._set_state(
            replace(
                self._state,
                connection_verified=False,
                verification_message="Connection not verified",
                firmware_version="Unknown",
            )
        )
        self._transport.open(SerialConfig(port=port, baudrate=baudrate))

    def list_ports(self) -> tuple[str, ...]:
        return tuple(port.system_location for port in self._transport.enumerate_ports())

    def disconnect_port(self) -> None:
        self._reset_verification_tracking()
        self._transport.close()

    def send_command(self, command: Command) -> None:
        self._append_log(f">> {command.encode()}")
        self._transport.send_line(command.encode())

    def set_selected_channels(self, channels: list[int]) -> None:
        mask = 0
        for channel in channels:
            mask |= channel_to_mask(channel)
        self._set_state(replace(self._state, selected_mask=mask & CHANNEL_MASK_ALL))

    def set_test_mode(self, test_mode: str) -> None:
        if test_mode not in {"all", "sequential"}:
            self._set_error(f"Unsupported test mode: {test_mode}")
            return
        self._set_state(
            replace(
                self._state,
                test_mode=test_mode,
                selected_action_mode_label=self._action_mode_label(test_mode),
            )
        )

    def set_wizard_test_kind(self, wizard_test_kind: Literal["simple", "advanced"] | None) -> None:
        if wizard_test_kind is not None and wizard_test_kind not in {"simple", "advanced"}:
            self._set_error(f"Unsupported wizard test mode: {wizard_test_kind}")
            return
        self._set_state(replace(self._state, selected_test_kind=wizard_test_kind))

    def go_previous_step(self) -> None:
        if self._state.test_progress.active:
            self._set_status_message("Cannot go back while test is running")
            return
        if self._state.wizard_step <= 0:
            self._set_status_message("Already on the first step")
            return
        self._set_state(
            replace(
                self._state,
                wizard_step=self._state.wizard_step - 1,
                status_message=f"Moved to step {self._state.wizard_step}",
            )
        )

    def go_next_step(self) -> None:
        if self._state.wizard_step >= 2:
            self._set_status_message("Already on the last step")
            return
        if self._state.wizard_step == 0:
            if not self._state.connected:
                self._set_status_message("Connect to a controller before continuing")
                return
            if not self._state.connection_verified:
                self._set_status_message("Verify the connection before continuing")
                return
        elif self._state.wizard_step == 1 and self._state.selected_test_kind is None:
            self._set_status_message("Choose Simple or Advanced before continuing")
            return
        self._set_state(
            replace(
                self._state,
                wizard_step=self._state.wizard_step + 1,
                status_message=f"Moved to step {self._state.wizard_step + 2}",
            )
        )

    def set_model(self, model: int) -> None:
        self.send_command(model_command(model))

    def refresh_status(self) -> None:
        self.send_command(status_command())

    def verify_connection(self) -> None:
        if not self._state.connected:
            self._set_error("Connect to a controller before verification")
            return
        self._begin_connection_verification("Verification in progress...")

    def request_help(self) -> None:
        self.send_command(help_command())

    def report_validation_error(self, message: str) -> None:
        self._set_error(message)

    def set_auto_poll_enabled(self, enabled: bool) -> None:
        self._set_state(replace(self._state, auto_poll_enabled=enabled))
        self._update_poll_timer()

    def set_auto_poll_interval_ms(self, interval_ms: int) -> None:
        self._set_state(replace(self._state, auto_poll_interval_ms=interval_ms))
        self._update_poll_timer()

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
        self._begin_test_tracking("continuous")
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
        self._clear_test_tracking("Stopped")
        self.send_command(stopall_command())

    def run_selected(self, pulses: int) -> None:
        pulses_error = self._validate_pulses(pulses)
        if pulses_error is not None:
            self._set_error(pulses_error)
            return
        self._append_log(
            "Selected-action compatibility fallback: expanding Run Selected into per-channel RUN commands."
        )
        self._begin_test_tracking("counted", pulses)
        for channel in self._state.selected_channels:
            self.send_command(run_command(channel, pulses))

    def run_selected_test(self, model: int, rpm: float, duty: float, pulses: int) -> None:
        if not self._state.selected_channels:
            self._set_error("Select at least one channel before starting a test")
            return

        rpm_error = self._validate_rpm(rpm)
        if rpm_error is not None:
            self._set_error(rpm_error)
            return

        duty_error = self._validate_duty(duty)
        if duty_error is not None:
            self._set_error(duty_error)
            return

        pulses_error = self._validate_pulses(pulses)
        if pulses_error is not None:
            self._set_error(pulses_error)
            return

        self.send_command(model_command(model))
        self.apply_channel_settings(rpm, duty)
        if self._state.test_mode == "sequential":
            self._append_log(
                "Sequential mode: counted-pulse tests will run one selected channel at a time."
            )
            self._begin_test_tracking("counted", pulses, execution_mode="sequential")
            self._sequential_pending_channels = self._state.selected_channels
            self._sequential_current_channel = None
            self._sequential_completed_channels = 0
            self._sequential_current_started = False
            self._start_next_sequential_counted_channel()
            return

        self.run_selected(pulses)

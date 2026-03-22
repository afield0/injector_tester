from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence


CHANNEL_MASK_ALL = 0x0F


def channel_to_mask(channel: int) -> int:
    if channel < 1 or channel > 4:
        raise ValueError("channel must be between 1 and 4")
    return 1 << (channel - 1)


def channels_to_mask(channels: Iterable[int]) -> int:
    mask = 0
    for channel in channels:
        mask |= channel_to_mask(channel)
    return mask & CHANNEL_MASK_ALL


def mask_to_channels(mask: int) -> tuple[int, ...]:
    mask &= CHANNEL_MASK_ALL
    return tuple(index + 1 for index in range(4) if mask & (1 << index))


class CommandName(str, Enum):
    HELP = "HELP"
    STATUS = "STATUS"
    MODEL = "MODEL"
    SET = "SET"
    START = "START"
    RUN = "RUN"
    STOP = "STOP"
    STARTALL = "STARTALL"
    STOPALL = "STOPALL"


@dataclass(frozen=True)
class Command:
    name: CommandName
    args: tuple[str, ...] = ()
    channel_mask: int = 0

    def encode(self) -> str:
        parts = [self.name.value, *self.args]
        return " ".join(parts)


def help_command() -> Command:
    return Command(CommandName.HELP)


def status_command() -> Command:
    return Command(CommandName.STATUS)


def model_command(model: int) -> Command:
    if model not in (0, 1):
        raise ValueError("model must be 0 or 1")
    return Command(CommandName.MODEL, (str(model),))


def set_command(channel: int, rpm: float, duty_percent: float) -> Command:
    return Command(
        CommandName.SET,
        (str(channel), f"{rpm:.1f}", f"{duty_percent:.1f}"),
        channel_mask=channel_to_mask(channel),
    )


def start_command(channel: int) -> Command:
    return Command(CommandName.START, (str(channel),), channel_mask=channel_to_mask(channel))


def run_command(channel: int, pulses: int) -> Command:
    if pulses < 1:
        raise ValueError("pulses must be positive")
    return Command(
        CommandName.RUN,
        (str(channel), str(pulses)),
        channel_mask=channel_to_mask(channel),
    )


def stop_command(channel: int) -> Command:
    return Command(CommandName.STOP, (str(channel),), channel_mask=channel_to_mask(channel))


def startall_command() -> Command:
    return Command(CommandName.STARTALL, channel_mask=CHANNEL_MASK_ALL)


def stopall_command() -> Command:
    return Command(CommandName.STOPALL, channel_mask=CHANNEL_MASK_ALL)


@dataclass(frozen=True)
class ChannelStatus:
    channel: int
    enabled: bool
    state: bool
    mode: str
    rpm: float
    duty: float
    on_ticks: int
    off_ticks: int
    ticks_left: int
    pulses_left: int
    stop_after_low: bool


@dataclass(frozen=True)
class HelpResponse:
    lines: tuple[str, ...]


@dataclass(frozen=True)
class ReadyResponse:
    message: str


@dataclass(frozen=True)
class OkResponse:
    command: CommandName
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErrorResponse:
    message: str


@dataclass(frozen=True)
class StatusResponse:
    model: int
    tick_us: int
    active_mask: int
    state_mask: int
    channels: tuple[ChannelStatus, ...]


Response = HelpResponse | ReadyResponse | OkResponse | ErrorResponse | StatusResponse


def _parse_hex(value: str) -> int:
    return int(value, 16)


def _parse_ok(line: str) -> OkResponse:
    parts = line.split()
    if len(parts) < 2:
        raise ValueError(f"invalid OK response: {line}")
    return OkResponse(CommandName(parts[1]), tuple(parts[2:]))


def _parse_channel_status(line: str) -> ChannelStatus:
    tokens = line.split()
    if len(tokens) < 12 or tokens[0] != "CH":
        raise ValueError(f"invalid channel status line: {line}")

    channel = int(tokens[1])
    fields: dict[str, str] = {}
    for token in tokens[2:]:
        if "=" not in token:
            raise ValueError(f"invalid channel field: {token}")
        key, value = token.split("=", 1)
        fields[key] = value

    return ChannelStatus(
        channel=channel,
        enabled=fields["enabled"] == "1",
        state=fields["state"] == "1",
        mode=fields["mode"],
        rpm=float(fields["rpm"]),
        duty=float(fields["duty"]),
        on_ticks=int(fields["onTicks"]),
        off_ticks=int(fields["offTicks"]),
        ticks_left=int(fields["ticksLeft"]),
        pulses_left=int(fields["pulsesLeft"]),
        stop_after_low=fields["stopAfterLow"] == "1",
    )


class ResponseParser:
    """Parses the firmware's line-oriented protocol, including multi-line blocks."""

    def __init__(self) -> None:
        self._pending_help: list[str] | None = None
        self._pending_status: list[str] | None = None

    def feed_line(self, line: str) -> list[Response]:
        clean = line.strip()

        if self._pending_help is not None:
            if not clean or self._is_help_line(clean):
                self._pending_help.append(clean)
                return []
            pending = HelpResponse(tuple(self._pending_help))
            self._pending_help = None
            return [pending, *self.feed_line(clean)]

        if self._pending_status is not None:
            if not clean:
                return []
            self._pending_status.append(clean)
            if len(self._pending_status) == 8:
                return [self._finish_status()]
            return []

        if not clean:
            return []

        if clean == "Commands:":
            self._pending_help = [clean]
            return []

        if clean.startswith("MODEL ") and self._looks_like_status_header(clean):
            self._pending_status = [clean]
            return []

        if clean.startswith("OK "):
            return [_parse_ok(clean)]

        if clean.startswith("ERR "):
            return [ErrorResponse(clean[4:])]

        if clean == "Injector mask-ISR controller ready":
            return [ReadyResponse(clean)]

        return [ErrorResponse(f"Unrecognized response line: {clean}")]

    @staticmethod
    def _looks_like_status_header(line: str) -> bool:
        parts = line.split()
        return len(parts) == 2 and parts[1].isdigit()

    @staticmethod
    def _is_help_line(line: str) -> bool:
        return (
            line == "Models:"
            or line in {name.value for name in CommandName}
            or line.startswith("0 = ")
            or line.startswith("1 = ")
            or any(line.startswith(f"{name.value} ") for name in CommandName)
        )

    def _finish_status(self) -> StatusResponse:
        assert self._pending_status is not None
        lines = self._pending_status
        self._pending_status = None

        model = int(lines[0].split()[1])
        tick_us = int(lines[1].split()[1])
        active_mask = _parse_hex(lines[2].split()[1])
        state_mask = _parse_hex(lines[3].split()[1])
        channels = tuple(_parse_channel_status(line) for line in lines[4:])
        return StatusResponse(model, tick_us, active_mask, state_mask, channels)


def describe_response(response: Response) -> str:
    if isinstance(response, ReadyResponse):
        return response.message
    if isinstance(response, ErrorResponse):
        return f"ERR {response.message}"
    if isinstance(response, HelpResponse):
        return "\n".join(response.lines)
    if isinstance(response, OkResponse):
        detail = " ".join(response.detail).strip()
        return f"OK {response.command.value}" + (f" {detail}" if detail else "")
    if isinstance(response, StatusResponse):
        return (
            f"MODEL {response.model}, TICK_US {response.tick_us}, "
            f"ACTIVE_MASK 0x{response.active_mask:X}, STATE_MASK 0x{response.state_mask:X}"
        )
    raise TypeError(f"unsupported response type: {type(response)!r}")


def available_commands() -> Sequence[CommandName]:
    return tuple(CommandName)

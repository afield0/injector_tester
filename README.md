# injector_tester

Fuel Injection Test Bench

## Desktop app architecture

The repository now includes a PySide6 desktop client in [desktop_app](desktop_app) with four explicit layers:

- `ui.py`: Qt widgets only. It renders immutable view state and forwards user intent.
- `transport.py`: Serial I/O only. It owns backend selection, OS-specific serial behavior, line delivery, and protocol event emission.
- `protocol.py`: Firmware command builders and response parsers for `HELP`, `STATUS`, `MODEL`, `SET`, `START`, `RUN`, `STOP`, `STARTALL`, and `STOPALL`.
- `state.py`: Application controller and immutable app state. Selected channels are stored as a 4-bit mask for future grouped operations.

## Packaging

The desktop client is organized as a normal Python package with:

- package module: [desktop_app](desktop_app)
- callable entry point: [desktop_app/main.py](desktop_app/main.py)
- `python -m` entry point: [desktop_app/__main__.py](desktop_app/__main__.py)
- install metadata and GUI script: [pyproject.toml](pyproject.toml)

PyInstaller packaging is intentionally deferred until the grouped-command workflow is stable. Until then, the focus is keeping runtime behavior and serial semantics correct.

Grouped selected-channel command semantics are defined as follows: `Start Selected` and `Run Selected` initialize all selected outputs from the inactive phase and apply timing state together as part of one command handling path. This is the operator-visible meaning of grouped selected-channel actions once mask commands are used end-to-end.

The desktop GUI now exposes two operator-facing test modes:

- `All`: all checked channels receive the current configuration and run together for the requested pulse count.
- `Sequential`: the GUI runs checked channels one at a time for counted-pulse tests, advancing automatically after each channel finishes.

## UI Buttons

Operator-facing button meanings:

- `Connect`: Open the selected serial port at the application baud rate and begin reading firmware responses.
- `Disconnect`: Close the current serial port connection.
- `Refresh Ports`: Re-scan available serial ports and update the port selector.
- `Run Selected`: Apply the current model, RPM, duty, and pulse count to the checked channels, then run only those checked channels using the selected test mode.
- `Test Mode`: Choose whether checked channels run together (`All`) or one at a time (`Sequential`).
- `Stop All`: Stop all four channels, ignoring which channel checkboxes are selected. This is the operator’s global stop action and is intentionally more visually prominent in the UI.
- `Read Status`: Request live firmware status and refresh the summary and per-channel status table.
- `Help`: Request the firmware help text and protocol summary.

Important operator differences:

- `Run Selected` is the only selected-channel action button. It uses the checkbox selection and always applies the current configuration before output activity starts.
- `Stop All` ignores the checkbox selection and stops every output. It is the broad stop action for the whole bench and should be treated as the immediate all-channel stop.
- The GUI now runs counted-pulse tests only.
- In `All`, `Run Selected` applies config and runs all checked channels for the configured pulse count together.
- In `Sequential`, the GUI automatically runs `CH1`, `CH2`, `CH3`, and `CH4` in the order selected by the checkboxes' channel numbers, skipping unchecked channels.
- `Read Status` and `Help` are informational. They do not change injector output state.

## Run

Install dependencies:

```bash
python -m pip install -e .
```

Start the desktop app:

```bash
python -m desktop_app
```

Or, after installation:

```bash
injector-tester
```

By default the UI points at `/dev/ttyACM0`; change the port in the connection bar as needed.

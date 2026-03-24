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

Default desktop behavior:

- `Sequential` is the default test mode.
- Auto-polling is enabled by default at `1.0 s`.
- Auto-poll controls are available on the execution step.

## Wizard Flow

The desktop GUI now uses a 3-step wizard:

1. `Connection`: choose a serial port, connect, and verify communication with the controller.
2. `Test Type`: select `Simple` or `Advanced`, then configure only the controls for the selected mode.
3. `Execute`: start or cancel the test and monitor progress and channel state.

Connection requirements:

- Step 1 must be completed before advancing.
- `Next` remains disabled until the controller is connected and connection verification succeeds.
- Verification is required before the test setup pages can be used.

Test configuration:

- `Simple` reuses the basic counted-test inputs for model, RPM, duty, and pulse count.
- `Advanced` reuses the calculated test setup with derived duty cycle and pulse count.
- `Test Mode` still controls whether checked channels run together (`All`) or one at a time (`Sequential`).
- Only the selected mode's configuration UI is rendered on step 2.

Execution page:

- `Start Test` applies the selected mode's settings to the checked channels and starts the run.
- `Cancel` calls the global stop action and stops all channels immediately.
- The status area below the buttons shows the progress label, progress bar, firmware summary, and per-channel status table.
- Auto-poll controls are also available on this page while monitoring an active run.

Navigation rules:

- Back-navigation is blocked while `test_progress.active` is true.
- The mode selector and wizard navigation controls are disabled during an active test.
- Back-navigation is re-enabled automatically when the test is no longer active.

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

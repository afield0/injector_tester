# injector_tester

Fuel Injection Test Bench

## Desktop app architecture

The repository now includes a PySide6 desktop client in [desktop_app](/home/adam/dev/injector_tester/desktop_app) with four explicit layers:

- `ui.py`: Qt widgets only. It renders immutable view state and forwards user intent.
- `transport.py`: Serial I/O only. It owns backend selection, OS-specific serial behavior, line delivery, and protocol event emission.
- `protocol.py`: Firmware command builders and response parsers for `HELP`, `STATUS`, `MODEL`, `SET`, `START`, `RUN`, `STOP`, `STARTALL`, and `STOPALL`.
- `state.py`: Application controller and immutable app state. Selected channels are stored as a 4-bit mask for future grouped operations.

## Packaging

The desktop client is organized as a normal Python package with:

- package module: [desktop_app](/home/adam/dev/injector_tester/desktop_app)
- callable entry point: [desktop_app/main.py](/home/adam/dev/injector_tester/desktop_app/main.py)
- `python -m` entry point: [desktop_app/__main__.py](/home/adam/dev/injector_tester/desktop_app/__main__.py)
- install metadata and GUI script: [pyproject.toml](/home/adam/dev/injector_tester/pyproject.toml)

PyInstaller packaging is intentionally deferred until the grouped-command workflow is stable. Until then, the focus is keeping runtime behavior and serial semantics correct.

Grouped selected-channel command semantics are defined as follows: `Start Selected` and `Run Selected` initialize all selected outputs from the inactive phase and apply timing state together as part of one command handling path. This is the operator-visible meaning of grouped selected-channel actions once mask commands are used end-to-end.

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

"""Injector tester desktop application package."""


def main() -> int:
    """Lazily import the Qt entry point so non-UI modules stay importable in tests."""

    from .main import main as _main

    return _main()


__all__ = ["main"]

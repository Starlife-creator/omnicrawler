"""PyInstaller entry point for the companion command-line executable."""

from omnicrawl.core.runtime_paths import configure_runtime_environment

configure_runtime_environment()
from omnicrawl.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

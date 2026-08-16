"""PyInstaller entry point for the companion worker executable."""

from omnicrawl.core.runtime_paths import configure_runtime_environment

configure_runtime_environment()
from omnicrawl.runtime.worker_main import main

if __name__ == "__main__":
    raise SystemExit(main())

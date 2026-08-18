"""PyInstaller entry point for the desktop GUI."""

from omnicrawler.core.runtime_paths import configure_runtime_environment

configure_runtime_environment()
from omnicrawler.gui.main import main

if __name__ == "__main__":
    raise SystemExit(main())

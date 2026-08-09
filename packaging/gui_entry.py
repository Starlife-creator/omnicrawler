"""PyInstaller entry point for the desktop GUI."""

from omnicrawl.core.runtime_paths import configure_runtime_environment

configure_runtime_environment()
from omnicrawl.gui.main import main


if __name__ == "__main__":
    raise SystemExit(main())

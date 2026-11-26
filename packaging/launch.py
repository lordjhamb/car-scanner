"""Entry point for the packaged desktop app (PyInstaller)."""
import sys

from carscan.desktop import main

if __name__ == "__main__":
    sys.exit(main())

"""Entry point for Music Library Integrity Manager application."""

import sys
from src.gui.main_window import launch_gui


def main():
    sys.exit(launch_gui())


if __name__ == "__main__":
    main()

"""Restore this prototype only. No application launch or Steam operations."""

import sys

from install_patch import main as install_main


def main():
    sys.argv.insert(1, "restore")
    install_main()


if __name__ == "__main__":
    main()

"""Module entrypoint for `python -m drclaw.tray`."""

from drclaw.config.loader import load_config
from drclaw.tray.app import run_tray
from drclaw.utils.helpers import get_data_dir


def main() -> None:
    config = load_config(get_data_dir() / "config.json")
    run_tray(config)


if __name__ == "__main__":
    main()

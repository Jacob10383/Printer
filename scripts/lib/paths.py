from __future__ import annotations

from pathlib import Path

# Base paths
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
CONFIGS_DIR = REPO_ROOT / "configs"
BINARIES_DIR = REPO_ROOT / "binaries"
SERVICES_DIR = REPO_ROOT / "services"
PATCHES_DIR = REPO_ROOT / "patches"

# Target device paths (single-target, so constants are fine)
UDISK_ROOT = Path("/mnt/UDISK")
ROOT_HOME = UDISK_ROOT / "root"
PRINTER_DATA_DIR = UDISK_ROOT / "printer_data"
MOONRAKER_CONFIG = PRINTER_DATA_DIR / "config" / "moonraker.conf"
MOONRAKER_ASVC = PRINTER_DATA_DIR / "moonraker.asvc"
CUSTOM_CONFIG_DIR = PRINTER_DATA_DIR / "config" / "custom"
GUPPY_DIR = ROOT_HOME / "guppyscreen"
MAINTENANCE_DIR = ROOT_HOME / "mainsail"
KLIPPER_EXTRAS_DIR = ROOT_HOME / "klipper/klippy/extras"


def repo_path(*parts: str | Path) -> Path:
    """Convenience helper to join paths relative to REPO_ROOT."""
    return REPO_ROOT.joinpath(*map(Path, parts))

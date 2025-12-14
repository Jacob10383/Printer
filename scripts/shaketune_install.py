#!/usr/bin/env python3

import os
import shutil
import sys
import time
from contextlib import suppress
from pathlib import Path
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import shell  # noqa: E402
from lib.file_ops import copy_file, ensure_directory  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.paths import BINARIES_DIR, PATCHES_DIR  # noqa: E402

logger = get_logger("shaketune")

ROOT_HOME = Path("/mnt/UDISK/root")
KLIPPY_ENV = Path("/usr/share/klippy-env")
KLIPPY_PYTHON = KLIPPY_ENV / "bin" / "python"
KLIPPY_PIP = KLIPPY_ENV / "bin" / "pip"
SITE_PACKAGES = KLIPPY_ENV / "lib" / "python3.9" / "site-packages"
UDISK_SITE_PACKAGES = Path("/mnt/UDISK/klippy-env-site-packages")

KLIPPER_DIR = Path("/usr/share/klipper")
KLIPPER_EXTRAS_DIR = KLIPPER_DIR / "klippy" / "extras"
ROOT_KLIPPER_EXTRAS_DIR = ROOT_HOME / "klipper" / "klippy" / "extras"
SHAKETUNE_REPO = ROOT_HOME / "klippain_shaketune"
SHAKETUNE_REPO_URL = "https://github.com/Jacob10383/klippain-shaketune.git"

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
PIWHEELS_LINE = "extra-index-url=https://www.piwheels.org/simple"
LIB_NAMES = ["libgfortran.so.5.0.0", "libgfortran.so.5", "libgfortran.so.4", "libopenblas.so.0"]
GIT_DEST = Path("/usr/bin/git")
GIT_SOURCE = Path("/opt/bin/git")

PYTHON_REQUIREMENTS = [
    "GitPython==3.1.41",
    "python-dateutil==2.8.2",
    "packaging==23.2",
    "cycler==0.12.1",
    "fonttools==4.46.0",
    "importlib-resources==5.12.0",
    "zipp==3.23.0",
    "numpy==1.26.2",
    "scipy==1.11.4",
    "matplotlib==3.8.2",
    "PyWavelets==1.6.0",
    "zstandard==0.23.0",
    "contourpy==1.2.0",
    "kiwisolver==1.4.7",
    "Pillow==10.3.0",
]


def ensure_environment() -> bool:
    if not KLIPPY_ENV.exists():
        logger.error("Klippy virtualenv not found at %s", KLIPPY_ENV)
        return False
    if not KLIPPY_PYTHON.exists():
        logger.error("Python interpreter missing in %s", KLIPPY_PYTHON)
        return False
    ensure_directory(ROOT_HOME)
    ensure_directory(UDISK_SITE_PACKAGES)
    
    # Add .pth file to link UDISK packages
    pth_file = SITE_PACKAGES / "shaketune_udisk.pth"
    if SITE_PACKAGES.exists() and not pth_file.exists():
        try:
            pth_file.write_text(f"{UDISK_SITE_PACKAGES}\n")
            logger.info("Created .pth file at %s to link UDISK packages", pth_file)
        except Exception as exc:
            logger.warning("Failed to create .pth file: %s", exc)
            
    return True




def ensure_git_available() -> bool:
    if GIT_DEST.exists() or GIT_DEST.is_symlink():
        return True
    if not GIT_SOURCE.exists():
        logger.warning("Git binary not found at %s; Klipper may warn when retrieving versions", GIT_SOURCE)
        return False
    try:
        GIT_DEST.symlink_to(GIT_SOURCE)
        logger.info("Linked %s -> %s to make git available in PATH", GIT_DEST, GIT_SOURCE)
    except OSError as exc:
        logger.error("Failed to link %s -> %s: %s", GIT_DEST, GIT_SOURCE, exc)
        return False
    return True


def deploy_shaketune_repo() -> bool:
    if SHAKETUNE_REPO.exists():
        git_dir = SHAKETUNE_REPO / ".git"
        if git_dir.exists() or git_dir.is_file():
            # Check if remote URL matches
            result = shell.run_logged(
                ["git", "-C", str(SHAKETUNE_REPO), "remote", "get-url", "origin"],
                logger_name="shaketune",
            )
            if result.ok and result.stdout.strip() == SHAKETUNE_REPO_URL:
                logger.info("Existing Shake&Tune repo found with matching remote; updating...")
                # Fetch latest changes
                fetch_result = shell.run_logged(
                    ["git", "-C", str(SHAKETUNE_REPO), "fetch", "origin"],
                    logger_name="shaketune",
                )
                if not fetch_result.ok:
                    logger.warning("Failed to fetch updates; will clone fresh")
                else:
                    # Reset to match remote main branch
                    reset_result = shell.run_logged(
                        ["git", "-C", str(SHAKETUNE_REPO), "reset", "--hard", "origin/main"],
                        logger_name="shaketune",
                    )
                    if reset_result.ok:
                        logger.info("Successfully updated Shake&Tune repository")
                        return True
                    else:
                        logger.warning("Failed to reset to origin/main; will clone fresh")
            else:
                logger.info("Existing repo has different remote URL; will clone fresh")

        # Backup and clone fresh if update failed or remote doesn't match
        backup = SHAKETUNE_REPO.with_name(f"{SHAKETUNE_REPO.name}.bak.{int(time.time())}")
        logger.info("Backing up existing directory to %s", backup)
        shutil.move(SHAKETUNE_REPO, backup)

    logger.info("Cloning Klippain Shake&Tune repository...")
    result = shell.run_logged(["git", "clone", SHAKETUNE_REPO_URL, str(SHAKETUNE_REPO)], logger_name="shaketune")
    if not result.ok:
        logger.error("Failed to clone Shake&Tune repository")
        return False
    return True


def download_get_pip(dest: Path) -> bool:
    logger.info("Downloading get-pip.py from %s", GET_PIP_URL)
    try:
        with request.urlopen(GET_PIP_URL, timeout=60) as resp:
            data = resp.read()
    except error.URLError as exc:
        logger.error("Unable to download get-pip.py: %s", exc)
        return False
    ensure_directory(dest.parent)
    dest.write_bytes(data)
    return True


def refresh_pip() -> bool:
    get_pip_path = ROOT_HOME / "get-pip.py"
    if not download_get_pip(get_pip_path):
        return False

    logger.info("Refreshing pip inside %s", KLIPPY_ENV)
    result = shell.run_logged([str(KLIPPY_PYTHON), str(get_pip_path)], logger_name="shaketune")
    with suppress(FileNotFoundError):
        get_pip_path.unlink()
    if not result.ok:
        logger.error("get-pip.py failed to run")
        return False
    return True


def get_pip_version() -> tuple[int, int, int]:
    if not KLIPPY_PIP.exists():
        return (0, 0, 0)
    result = shell.run_logged([str(KLIPPY_PIP), "--version"], logger_name="shaketune", capture_output=True)
    if not result.ok or not result.stdout:
        return (0, 0, 0)
    try:
        parts = result.stdout.split()[1].split(".")
        parts += ["0"] * (3 - len(parts))
        return tuple(int(part) for part in parts[:3])
    except Exception:
        return (0, 0, 0)


def ensure_piwheels_index() -> bool:
    pip_conf = Path("/etc/pip.conf")
    ensure_directory(pip_conf.parent)

    existing = pip_conf.read_text().splitlines() if pip_conf.exists() else []
    if any(line.strip() == PIWHEELS_LINE for line in existing):
        logger.info("piwheels entry already present in %s", pip_conf)
        return True

    logger.info("Adding piwheels extra-index to %s", pip_conf)
    with pip_conf.open("a") as fh:
        if existing and existing[-1] and not existing[-1].endswith("\n"):
            fh.write("\n")
        fh.write(f"{PIWHEELS_LINE}\n")
    return True


def install_python_requirements() -> bool:
    if not KLIPPY_PIP.exists():
        logger.error("pip executable not found inside %s", KLIPPY_PIP)
        return False

    # Use UDISK for temp and cache to avoid filling rootfs
    udisk_tmp = Path("/mnt/UDISK/tmp")
    udisk_cache = Path("/mnt/UDISK/.pip_cache")
    ensure_directory(udisk_tmp)
    ensure_directory(udisk_cache)

    logger.info("Installing Shake&Tune Python requirements to UDISK...")
    logger.info("Using TMPDIR=%s and Cache=%s", udisk_tmp, udisk_cache)

    command = [
        str(KLIPPY_PIP), "install",
        "--upgrade",
        "--progress-bar", "off",
        "--target", str(UDISK_SITE_PACKAGES),
        "--cache-dir", str(udisk_cache),
        *PYTHON_REQUIREMENTS
    ]
    
    
    os.environ["TMPDIR"] = str(udisk_tmp)
    
    returncode = shell.stream_command(command, prefix="pip")
    
    # Clean up env just in case
    del os.environ["TMPDIR"]
    
    if returncode != 0:
        logger.error("pip install failed")
        return False
    logger.info("pip install completed successfully")
    return True


def patch_heaters_np_int() -> bool:
    heaters = Path("/usr/share/klipper/klippy/extras/heaters.py")
    if not heaters.exists():
        logger.warning("heaters.py not found at %s; skipping patch", heaters)
        return True

    text = heaters.read_text()
    old = "self.info_array = np.array(self._info_array, dtype=np.int)"
    if old not in text:
        logger.info("heaters.py already patched; skipping")
        return True

    heaters.write_text(text.replace(old, "self.info_array = np.array(self._info_array, dtype=int)", 1))
    logger.info("Patched np.int usage in heaters.py")
    return True


def install_custom_shaper_calibrate() -> bool:
    """Install custom shaper_calibrate.py patch with validation."""
    source = PATCHES_DIR / "shaper_calibrate.py"
    destination = ROOT_KLIPPER_EXTRAS_DIR / "shaper_calibrate.py"

    if not source.exists():
        logger.error("Custom shaper_calibrate.py not found at %s", source)
        return False
    
    src_size = source.stat().st_size
    if src_size == 0:
        logger.error("Source file %s is 0 bytes! Cannot install corrupted patch.", source)
        return False

    logger.info("Installing patched shaper_calibrate.py (%d bytes) to %s", src_size, destination)
    try:
        copy_file(source, destination)
        
        # Verify the installation
        if not destination.exists():
            logger.error("Installation failed: %s does not exist after copy", destination)
            return False
        
        dst_size = destination.stat().st_size
        if dst_size != src_size:
            logger.error(
                "Installation failed: size mismatch. Expected %d bytes, got %d bytes",
                src_size, dst_size
            )
            destination.unlink(missing_ok=True)
            return False
        
        logger.info("Successfully installed shaper_calibrate.py (%d bytes)", dst_size)
        
    except Exception as exc:
        logger.error("Failed to install shaper_calibrate.py: %s", exc)
        if destination.exists():
            destination.unlink(missing_ok=True)
        return False
    
    return True


def create_cpython_symlinks() -> None:
    suffix = ".cpython-39-arm-linux-gnueabihf.so"
    for root in (SITE_PACKAGES, UDISK_SITE_PACKAGES):
        if not root.exists():
            continue
        for so_path in root.rglob(f"*{suffix}"):
            replacement = so_path.with_name(so_path.name.replace(suffix, ".cpython-39.so"))
            if replacement.exists():
                continue
            rel_target = os.path.relpath(so_path, replacement.parent)
            replacement.symlink_to(rel_target)
    logger.info("Ensured .cpython-39.so compatibility symlinks exist")


def install_runtime_libs() -> bool:
    """Install required runtime libraries with comprehensive validation."""
    for name in LIB_NAMES:
        src = BINARIES_DIR / name
        dst = Path("/usr/lib") / name
        
        # Check if library already exists (e.g., from carto installer)
        if dst.exists() and not dst.is_symlink():
            dst_size = dst.stat().st_size
            logger.info("Library %s already exists at %s (%d bytes); skipping", name, dst, dst_size)
            
            # Warn if existing file is suspiciously small
            if dst_size == 0:
                logger.warning("WARNING: Existing library %s is 0 bytes! This may indicate a previous failed installation.", dst)
            
            continue
            
        if not src.exists():
            if not dst.exists():
                logger.error(
                    "Required runtime library %s missing from %s and not found in %s. "
                    "Please add %s to the binaries directory.",
                    name, BINARIES_DIR, dst.parent, name
                )
                return False
            # dst exists as symlink, we'll use it
            logger.info("Library %s exists as symlink at %s; will use existing", name, dst)
            continue
        
        # Get source file info for validation
        src_size = src.stat().st_size
        logger.info("Preparing to copy %s (%d bytes) to %s", src, src_size, dst)
        
        if src_size == 0:
            logger.error("Source library %s is 0 bytes! Cannot install corrupted library.", src)
            return False
        
        # Perform the copy with verification
        try:
            copy_file(src, dst)
            
            # Double-check the copy succeeded
            if not dst.exists():
                logger.error("CRITICAL: Copy operation reported success but destination file %s does not exist!", dst)
                return False
            
            dst_size = dst.stat().st_size
            logger.info("Successfully copied %s: %d bytes -> %d bytes", name, src_size, dst_size)
            
            # Verify sizes match
            if dst_size != src_size:
                logger.error(
                    "CRITICAL: Size mismatch after copying %s! Source: %d bytes, Destination: %d bytes",
                    name, src_size, dst_size
                )
                # Clean up the corrupted file
                dst.unlink(missing_ok=True)
                return False
            
            if dst_size == 0:
                logger.error("CRITICAL: Destination file %s is 0 bytes after copy!", dst)
                # Clean up the corrupted file
                dst.unlink(missing_ok=True)
                return False
                
        except Exception as exc:
            logger.error("Failed to copy %s to %s: %s", src, dst, exc)
            # Clean up any partial copy
            if dst.exists():
                dst.unlink(missing_ok=True)
            return False

    # Create libgfortran symlink if needed
    target = Path("/usr/lib/libgfortran.so.5.0.0")
    symlink = Path("/usr/lib/libgfortran.so.5")
    
    if not target.exists():
        logger.warning("Target library %s does not exist; cannot create symlink", target)
    else:
        target_size = target.stat().st_size
        if target_size == 0:
            logger.error("CRITICAL: Target library %s is 0 bytes! Cannot create symlink to corrupted file.", target)
            return False
        
        try:
            if symlink.exists() or symlink.is_symlink():
                symlink.unlink()
            symlink.symlink_to(target.name)
            logger.info("Linked %s -> %s (target: %d bytes)", symlink, target.name, target_size)
            
            # Verify the symlink was created correctly
            if not symlink.is_symlink():
                logger.error("Failed to create symlink %s -> %s", symlink, target.name)
                return False
                
        except Exception as exc:
            logger.error("Failed to create symlink %s -> %s: %s", symlink, target.name, exc)
            return False
    
    logger.info("All runtime libraries installed and verified successfully")
    return True


def link_into_klipper() -> bool:
    target = SHAKETUNE_REPO / "shaketune"
    if not target.exists():
        logger.error("Shake&Tune source directory missing at %s", target)
        return False

    ensure_directory(KLIPPER_EXTRAS_DIR)
    destination = KLIPPER_EXTRAS_DIR / "shaketune"

    if destination.is_symlink() or destination.exists():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()

    destination.symlink_to(target)
    logger.info("Linked %s -> %s", destination, target)
    return True


def install_shaketune() -> bool:
    if not ensure_environment():
        return False
    
    ensure_git_available()
    if not deploy_shaketune_repo():
        return False
    pip_version = get_pip_version()
    if pip_version < (20, 0, 0):
        if not refresh_pip():
            return False
    else:
        logger.info("pip %s.%s.%s already sufficient; skipping refresh", *pip_version)
    if not ensure_piwheels_index():
        return False
    if not install_python_requirements():
        logger.warning("Initial pip install failed; refreshing pip and retrying once")
        if not refresh_pip():
            return False
        if not install_python_requirements():
            return False

    create_cpython_symlinks()
    if not patch_heaters_np_int():
        logger.warning("Failed to patch heaters.py; continuing")
    if not install_custom_shaper_calibrate():
        logger.warning("Failed to install patched shaper_calibrate.py; continuing")


    create_cpython_symlinks()

    if not install_runtime_libs():
        return False
    if not link_into_klipper():
        return False

    logger.info("Shake&Tune installation completed successfully")
    return True


def main() -> None:
    if os.geteuid() != 0:
        logger.error("This installer must be run as root (use sudo)")
        sys.exit(1)

    if not install_shaketune():
        sys.exit(1)


if __name__ == "__main__":
    main()

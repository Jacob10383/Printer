#!/usr/bin/env python3

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Ensure our shared helper modules are importable when executing as a script
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

os.environ.setdefault("FORCE_COLOR", "1")

from lib import shell  # noqa: E402
from lib.logging_utils import get_logger, log_section, log_success  # noqa: E402
from lib.paths import REPO_ROOT  # noqa: E402


@dataclass
class ComponentResult:
    name: str
    success: bool
    details: Optional[str] = None


class PrinterInstaller:
    def __init__(self) -> None:
        self.logger = get_logger("install")

    # ------------------------------------------------------------------ #
    # Git helpers
    def run_command(self, command: str):
        return shell.run_logged(command, logger_name="install")

    def is_git_repository(self, path: str) -> bool:
        return os.path.isdir(os.path.join(path, ".git"))

    def get_current_git_branch(self, repo_path: str) -> Optional[str]:
        result = self.run_command(f"git -C '{repo_path}' rev-parse --abbrev-ref HEAD")
        if result and result.ok:
            branch = result.stdout.strip()
            return branch or None
        return None

    def prompt_user_conflict_resolution(self) -> str:
        """Prompt the user for how to resolve git pull conflicts."""
        while True:
            print("\nGit pull encountered conflicts or failed.")
            print("Choose an option:")
            print("  [a] Abort install (do nothing)")
            print("  [f] Force pull (discard local changes) and install")
            print("  [i] Install without pulling")
            choice = input("Enter choice [a/f/i]: ").strip().lower()
            if choice in ("a", "f", "i"):
                return choice
            print("Invalid choice. Please enter 'a', 'f', or 'i'.")

    def get_head_sha(self, repo_path: str) -> Optional[str]:
        result = shell.run(f"git -C '{repo_path}' rev-parse HEAD")
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None

    def update_repository(self) -> tuple[bool, bool]:
        """Detect repo root and attempt to pull latest changes from origin.

        Returns (continue_installation, restart_required).
        """
        repo_path = str(REPO_ROOT)
        self.logger.info("Detected repository path: %s", repo_path)

        if not self.is_git_repository(repo_path):
            self.logger.info("Not a Git repository. Skipping git pull.")
            return True, False

        head_before = self.get_head_sha(repo_path)

        current_branch = self.get_current_git_branch(repo_path)
        if not current_branch:
            self.logger.warning("Unable to determine current git branch. Skipping git pull.")
            return True, False

        self.logger.info("Current branch: %s", current_branch)
        pull_result = self.run_command(f"git -C '{repo_path}' pull origin {current_branch}")
        if pull_result and pull_result.ok:
            self.logger.info("Repository updated successfully.")
            head_after = self.get_head_sha(repo_path)
            restart_needed = bool(head_before and head_after and head_before != head_after)
            return True, restart_needed

        choice = self.prompt_user_conflict_resolution()
        if choice == "a":
            self.logger.warning("User chose to abort installation due to git conflicts.")
            return False, False
        if choice == "i":
            self.logger.warning("Proceeding with installation without pulling updates.")
            return True, False

        self.logger.warning("Forcing repository to match origin (discarding local changes).")
        fetch_res = self.run_command(f"git -C '{repo_path}' fetch origin")
        if not fetch_res or not fetch_res.ok:
            self.logger.error("Failed to fetch from origin. Cannot force pull.")
            return False, False
        reset_res = self.run_command(f"git -C '{repo_path}' reset --hard origin/{current_branch}")
        if not reset_res or not reset_res.ok:
            self.logger.error("Failed to reset to origin. Cannot continue.")
            return False, False

        self.logger.info("Repository forcibly updated to match origin.")
        return True, True

    # ------------------------------------------------------------------ #
    # Component runners
    def run_installer(self, component_name: str, script_name: str, extra_args: Optional[List[str]] = None) -> bool:
        """Generic method to run any installer script."""
        installer_path = REPO_ROOT / "scripts" / script_name
        if not installer_path.exists():
            self.logger.error("%s install script not found at %s", component_name, installer_path)
            return False

        args_str = " " + " ".join(extra_args) if extra_args else ""
        command = f"PYTHONUNBUFFERED=1 python3 -u '{installer_path}'{args_str}"
        self.logger.info("Launching %s installer via: %s", component_name, command)
        try:
            prefix = f"[{component_name}]"
            return_code = shell.stream_command(command, prefix=prefix)
        except Exception as exc:
            self.logger.error("Failed to run %s installer: %s", component_name, exc)
            return False

        if return_code == 0:
            log_success(f"{component_name} installation completed successfully")
            return True

        self.logger.error("%s installation failed with return code %s", component_name, return_code)
        return False

    # Individual component wrappers
    def install_ustreamer(self) -> bool:
        return self.run_installer("ustreamer", "ustreamer_install.py")

    def install_guppyscreen(self) -> bool:
        return self.run_installer("guppyscreen", "guppyscreen_install.py")

    def install_kamp(self) -> bool:
        return self.run_installer("KAMP", "kamp_install.py")

    def install_overrides(self) -> bool:
        return self.run_installer("overrides", "overrides_install.py")

    def install_cleanup_service(self) -> bool:
        return self.run_installer("cleanup service", "cleanup_install.py")

    def install_resonance_tester(self) -> bool:
        return self.run_installer("resonance tester", "resonance_install.py")

    def install_timelapse(self) -> bool:
        return self.run_installer("timelapse", "timelapse_install.py")

    def install_timelapse_h264(self) -> bool:
        return self.run_installer("timelapse (H264)", "timelapse_install.py", extra_args=["--encoder", "h264"])

    def install_mainsail(self) -> bool:
        return self.run_installer("mainsail", "mainsail_install.py")

    def install_shaketune(self) -> bool:
        return self.run_installer("shaketune", "shaketune_install.py")

    # ------------------------------------------------------------------ #
    def run_installation(self, components: Optional[List[str]] = None) -> bool:
        if components is None:
            components = ["guppyscreen", "ustreamer", "overrides", "cleanup", "resonance", "shaketune", "timelapse", "mainsail"]

        self.logger.info("Starting 3D Printer Installation...")
        self.logger.info("Components to install: %s", ", ".join(components))

        results: List[ComponentResult] = []

        def _record(name: str, installer_fn) -> None:
            log_section(f"Running {name} installer")
            success = installer_fn()
            results.append(ComponentResult(name, success))

        # Maintain same ordering as before
        if "guppyscreen" in components:
            _record("guppyscreen", self.install_guppyscreen)
        if "ustreamer" in components:
            _record("ustreamer", self.install_ustreamer)
        if "kamp" in components:
            _record("kamp", self.install_kamp)
        if "overrides" in components:
            _record("overrides", self.install_overrides)
        if "cleanup" in components:
            _record("cleanup", self.install_cleanup_service)
        if "resonance" in components:
            _record("resonance", self.install_resonance_tester)
        if "timelapse" in components:
            _record("timelapse", self.install_timelapse)
        if "timelapseh264" in components:
            _record("timelapse (H264)", self.install_timelapse_h264)
        if "mainsail" in components:
            _record("mainsail", self.install_mainsail)
        if "shaketune" in components:
            _record("shaketune", self.install_shaketune)

        self.logger.info("=" * 50)
        self.logger.info("INSTALLATION SUMMARY")
        self.logger.info("=" * 50)

        for result in results:
            status = "SUCCESS" if result.success else "FAILED"
            self.logger.info("%-18s : %s", result.name, status)

        all_success = all(result.success for result in results)
        if all_success:
            self.logger.info("All components installed successfully!")
        else:
            self.logger.warning("Some components failed to install. Check logs above.")

        return all_success


def main() -> None:
    parser = argparse.ArgumentParser(description="3D Printer Automated Installer")
    parser.add_argument(
        "--components",
        nargs="+",
        choices=["guppyscreen", "ustreamer", "kamp", "overrides", "cleanup", "resonance", "shaketune", "timelapse", "timelapseh264", "mainsail"],
        help="Specific components to install (default: all)",
    )

    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: This installer must be run as root (use sudo)")
        sys.exit(1)

    installer = PrinterInstaller()

    continue_install, needs_restart = installer.update_repository()
    if not continue_install:
        sys.exit(0)
    if needs_restart:
        installer.logger.info("Repository updated; relaunching installer to use new code...")
        os.execv(sys.executable, [sys.executable, *sys.argv])

    try:
        success = installer.run_installation(components=args.components)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nInstallation interrupted by user")
        sys.exit(1)
    except Exception as exc:
        print(f"\n\nInstallation failed with error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

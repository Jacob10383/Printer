#!/usr/bin/env python3

from __future__ import annotations

import os
import ssl
import certifi

# Fix SSL certificate verification for compiled apps
cert_path = certifi.where()
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['REQUESTS_CA_BUNDLE'] = cert_path
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=cert_path)

import contextlib
import json
import multiprocessing as mp
import queue
import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import flet as ft
import installer_worker



class PrinterInstallerGUI:
    """Main GUI application."""

    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Printer Installer"
        self.page.padding = 20
        self.page.theme_mode = ft.ThemeMode.DARK

        # Window sizing configuration
        self.page.window.width = 900
        self.page.window.min_width = 800
        self.page.window.max_width = 1200

        # Dynamic height based on content
        self.base_height = 560  # Height without log, keeps controls visible
        self.log_height = 160  # Additional height when log is shown
        self.page.window.height = self.base_height
        self.page.window.min_height = self.base_height
        self.page.window.max_height = 1000

        self.worker_process: mp.Process | None = None
        self.worker_queue: mp.Queue | None = None
        self.worker_listener: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None
        self.log_visible = False
        self.verbose_log = False
        self.full_log_buffer = ""  # Store concise console output
        self.detailed_log_buffer = ""  # Store complete detailed log
        self._programmatic_checkbox_change = (
            False  # Flag to ignore programmatic changes
        )

        # Get current working directory for file picker initial path
        self.current_dir = str(Path.cwd())

        # Refs
        self.ip_field = ft.Ref[ft.TextField]()
        self.branch_field = ft.Ref[ft.Dropdown]()
        self.reset_checkbox = ft.Ref[ft.Checkbox]()
        self.preserve_stats_checkbox = ft.Ref[ft.Checkbox]()
        self.restore_backup_checkbox = ft.Ref[ft.Checkbox]()
        self.bootstrap_checkbox = ft.Ref[ft.Checkbox]()
        self.k2_checkbox = ft.Ref[ft.Checkbox]()
        self.repo_checkbox = ft.Ref[ft.Checkbox]()
        self.backup_path_field = ft.Ref[ft.TextField]()
        self.backup_path_container = ft.Ref[ft.Container]()
        self.browse_btn = ft.Ref[ft.FilledButton]()
        self.log_container = ft.Ref[ft.Container]()
        self.log_view = ft.Ref[ft.ListView]()
        self.toggle_log_btn = ft.Ref[ft.IconButton]()
        self.verbose_checkbox = ft.Ref[ft.Checkbox]()
        self.download_log_btn = ft.Ref[ft.IconButton]()
        self.start_btn = ft.Ref[ft.ElevatedButton]()
        self.cancel_btn = ft.Ref[ft.ElevatedButton]()
        self.factory_reset_dialog = None
        self.backup_dialog = None
        self.backup_dialog_path_field = ft.Ref[ft.TextField]()
        self.create_factory_reset_dialog()
        self.create_backup_dialog()

        # File pickers
        self.backup_path_picker = ft.FilePicker(on_result=self.on_backup_path_selected)
        self.backup_save_picker = ft.FilePicker(on_result=self.on_backup_save_selected)
        self.restore_picker = ft.FilePicker(on_result=self.on_restore_path_selected)
        self.log_save_picker = ft.FilePicker(on_result=self.on_log_save_selected)
        self.page.overlay.append(self.backup_path_picker)
        self.page.overlay.append(self.backup_save_picker)
        self.page.overlay.append(self.restore_picker)
        self.page.overlay.append(self.log_save_picker)

        self.configure_multiprocessing()

        self.setup_ui()
        self.update_button_status("Start Installation", ft.Colors.BLUE, False)
        threading.Thread(target=self.load_branch_options, daemon=True).start()

    def configure_multiprocessing(self) -> None:
        """Configure multiprocessing to use sys.executable."""
        try:
            mp.set_start_method("spawn", force=True)
        except RuntimeError:
            pass

        mp.set_executable(sys.executable)

    def setup_ui(self):
        """Set up the GUI layout."""

        # Quick Actions - compact header
        quick_actions = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Tools:", size=14, weight=ft.FontWeight.W_500),
                            ft.ElevatedButton(
                                "🔑 Install SSH Key",
                                on_click=lambda _: self.run_command("ssh_key"),
                                height=36,
                            ),
                            ft.ElevatedButton(
                                "💾 Backup Moonraker Stats",
                                on_click=lambda _: self.show_backup_dialog(),
                                height=36,
                            ),
                            ft.ElevatedButton(
                                "↩️ Restore Moonraker Stats",
                                on_click=lambda _: self.quick_restore(),
                                height=36,
                            ),
                            ft.ElevatedButton(
                                "♻️ Factory Reset",
                                on_click=lambda _: self.show_factory_reset_dialog(),
                                height=36,
                            ),
                        ],
                        spacing=10,
                        wrap=True,
                    ),
                ],
                spacing=0,
            ),
            padding=12,
            border_radius=14,
            bgcolor=ft.Colors.SURFACE,
            margin=ft.margin.only(bottom=8),
        )

        # Installation Settings - Reorganized
        install_settings = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "Installation Settings", size=16, weight=ft.FontWeight.W_500
                    ),
                    # Printer IP
                    ft.TextField(
                        ref=self.ip_field,
                        label="Printer IP Address",
                        value="192.168.1.4",
                        dense=True,
                        width=300,
                    ),
                    # Installation Steps Section
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(
                                    "Installation Steps",
                                    size=13,
                                    weight=ft.FontWeight.W_500,
                                    color=ft.Colors.PRIMARY,
                                ),
                                ft.Row(
                                    [
                                        # Left column - checkboxes
                                        ft.Column(
                                            [
                                                ft.Container(
                                                    content=ft.Checkbox(
                                                        ref=self.bootstrap_checkbox,
                                                        label="Upload & Run Bootstrap",
                                                        value=True,
                                                        on_change=lambda _: self.on_step_checkbox_changed(
                                                            "bootstrap"
                                                        ),
                                                    ),
                                                    height=40,
                                                ),
                                                ft.Container(
                                                    content=ft.Checkbox(
                                                        ref=self.k2_checkbox,
                                                        label="Install K2 Improvements",
                                                        value=True,
                                                        on_change=lambda _: self.on_step_checkbox_changed(
                                                            "k2"
                                                        ),
                                                    ),
                                                    height=40,
                                                ),
                                                ft.Container(
                                                    content=ft.Row(
                                                        [
                                                            ft.Checkbox(
                                                                ref=self.repo_checkbox,
                                                                label="Clone & Install Repo",
                                                                value=True,
                                                                on_change=lambda _: self.on_step_checkbox_changed(
                                                                    "repo"
                                                                ),
                                                            ),
                                                            ft.Dropdown(
                                                                ref=self.branch_field,
                                                                label="Branch",
                                                                value="jac",
                                                                options=[
                                                                    ft.dropdown.Option(
                                                                        "jac"
                                                                    )
                                                                ],
                                                                dense=True,
                                                                width=150,
                                                            ),
                                                        ],
                                                        spacing=12,
                                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                    ),
                                                    height=40,
                                                ),
                                            ],
                                            spacing=8,
                                            expand=1,
                                        ),
                                        # Vertical divider
                                        ft.Container(
                                            width=1,
                                            bgcolor=ft.Colors.OUTLINE_VARIANT,
                                            margin=ft.margin.symmetric(horizontal=16),
                                        ),
                                        # Right column - reset and backup options
                                        ft.Column(
                                            [
                                                ft.Container(
                                                    content=ft.Checkbox(
                                                        ref=self.reset_checkbox,
                                                        label="Factory Reset Device",
                                                        value=False,
                                                        on_change=self.on_reset_toggled,
                                                    ),
                                                    height=40,
                                                ),
                                                ft.Container(
                                                    content=ft.Container(
                                                        content=ft.Checkbox(
                                                            ref=self.preserve_stats_checkbox,
                                                            label="↳ Preserve Moonraker Stats",
                                                            value=False,
                                                            disabled=True,
                                                            on_change=self.on_preserve_stats_toggled,
                                                        ),
                                                        padding=ft.padding.only(
                                                            left=28
                                                        ),
                                                    ),
                                                    height=40,
                                                ),
                                                ft.Container(
                                                    content=ft.Checkbox(
                                                        ref=self.restore_backup_checkbox,
                                                        label="Use Existing Backup for Installation",
                                                        value=False,
                                                        on_change=self.on_restore_backup_toggled,
                                                    ),
                                                    height=40,
                                                ),
                                                ft.Container(
                                                    ref=self.backup_path_container,
                                                    visible=False,
                                                    content=ft.Row(
                                                        [
                                                            ft.TextField(
                                                                ref=self.backup_path_field,
                                                                label="Backup Directory",
                                                                hint_text="/path/to/backup",
                                                                value="",
                                                                dense=True,
                                                                expand=1,
                                                            ),
                                                            ft.FilledButton(
                                                                ref=self.browse_btn,
                                                                text="Browse",
                                                                on_click=self.on_browse_button_click,
                                                                tooltip="Select backup directory",
                                                                height=36,
                                                            ),
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    padding=ft.padding.only(left=28),
                                                ),
                                            ],
                                            spacing=8,
                                            expand=1,
                                        ),
                                    ],
                                    vertical_alignment=ft.CrossAxisAlignment.START,
                                ),
                            ],
                            spacing=8,
                        ),
                        margin=ft.margin.only(top=8),
                    ),
                ],
                spacing=8,
            ),
            padding=12,
            border_radius=14,
            bgcolor=ft.Colors.SURFACE,
            margin=ft.margin.only(bottom=12),
        )

        # Action Buttons
        action_buttons = ft.Row(
            [
                ft.Container(expand=True),
                ft.ElevatedButton(
                    ref=self.start_btn,
                    text="Start Installation",
                    on_click=lambda _: self.run_command("install"),
                    style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE, color=ft.Colors.WHITE),
                    height=40,
                ),
                ft.ElevatedButton(
                    ref=self.cancel_btn,
                    text="Cancel",
                    on_click=self.cancel_installation,
                    visible=False,  # Hidden by default
                    style=ft.ButtonStyle(bgcolor=ft.Colors.RED, color=ft.Colors.WHITE),
                    height=40,
                ),
            ],
            spacing=10,
        )

        # Log Section - using expand for text field
        log_section = ft.Container(
            ref=self.log_container,
            visible=True,
            content=ft.Column(
                [
                    # Header with embedded controls
                    ft.Row(
                        [
                            ft.Text(
                                "Installation Log", size=16, weight=ft.FontWeight.W_500
                            ),
                            ft.Checkbox(
                                ref=self.verbose_checkbox,
                                label="Show Verbose Log",
                                value=False,
                                on_change=self.on_verbose_toggled,
                                visible=False,
                            ),
                            ft.Container(expand=True),
                            ft.IconButton(
                                ref=self.download_log_btn,
                                icon=ft.Icons.DOWNLOAD,
                                tooltip="Download Full Log File",
                                on_click=self.download_log,
                                icon_size=20,
                                visible=False,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                tooltip="Clear Log",
                                on_click=self.clear_log,
                                icon_size=20,
                            ),
                            ft.IconButton(
                                ref=self.toggle_log_btn,
                                icon=ft.Icons.OPEN_IN_FULL,
                                tooltip="Show Log",
                                on_click=self.toggle_log_visibility,
                                icon_size=20,
                            ),
                        ],
                        spacing=5,
                    ),
                    ft.Container(
                        content=ft.ListView(
                            ref=self.log_view,
                            expand=True,
                            spacing=0,
                            padding=5,
                            auto_scroll=True,
                        ),
                        expand=True,
                        visible=False,
                        bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
                        border_radius=8,
                    ),
                ],
                spacing=8,
                expand=True,
            ),
            padding=12,
            border_radius=14,
            bgcolor=ft.Colors.SURFACE,
            expand=True,
        )

        self.page.add(quick_actions, install_settings, action_buttons, log_section)
        self.update_branch_visibility()

    def adjust_window_height(self):
        """Dynamically adjust window height based on content."""
        if self.log_visible:
            self.page.window.height = self.base_height + self.log_height
        else:
            self.page.window.height = self.base_height
        self.page.update()

    def update_button_status(self, text: str, color: str, disabled: bool):
        """Update the start button to show status."""
        self.start_btn.current.text = text
        self.start_btn.current.style = ft.ButtonStyle(
            bgcolor=color, color=ft.Colors.WHITE
        )
        self.start_btn.current.disabled = disabled
        self.page.update()

    def show_snackbar(self, message: str, bgcolor=ft.Colors.BLUE):
        """Show notification."""
        self.page.snack_bar = ft.SnackBar(content=ft.Text(message), bgcolor=bgcolor)
        self.page.snack_bar.open = True
        self.page.update()

    def load_branch_options(self):
        """Fetch brancehs"""
        url = "https://api.github.com/repos/Jacob10383/Printer/branches"
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "PrinterInstallerGUI",
                },
            )
            
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read().decode("utf-8")
                data = json.loads(payload)
                
            branches = [
                item.get("name")
                for item in data
                if isinstance(item, dict) and item.get("name")
            ]
            
            if not branches:
                raise ValueError("No branches returned.")
                
        except Exception as exc:
            print(f"[WARN] Failed to load branches: {exc}")
            return
        
        dropdown = self.branch_field.current
        if not dropdown:
            return
        
        dropdown.options = [ft.dropdown.Option(branch) for branch in branches]
        default = dropdown.value or ""
        if default not in branches:
            default = "jac" if "jac" in branches else branches[0]
        dropdown.value = default
        self.page.update()

    def on_reset_toggled(self, e):
        """Toggle preserve stats checkbox and update backup restore state."""
        self.preserve_stats_checkbox.current.disabled = (
            not self.reset_checkbox.current.value
        )
        if not self.reset_checkbox.current.value:
            self.preserve_stats_checkbox.current.value = False
        self.update_backup_restore_state()
        self.page.update()

    def on_preserve_stats_toggled(self, e):
        """Update backup restore state when preserve stats is toggled."""
        self.update_backup_restore_state()
        self.page.update()

    def update_backup_restore_state(self):
        """Disable restore backup checkbox if preserve stats is enabled."""
        preserve_enabled = (
            self.preserve_stats_checkbox.current.value
            and not self.preserve_stats_checkbox.current.disabled
        )

        if preserve_enabled:
            # Disable and uncheck restore backup
            self.restore_backup_checkbox.current.disabled = True
            self.restore_backup_checkbox.current.value = False
            if self.backup_path_container.current:
                self.backup_path_container.current.visible = False
        else:
            # Enable restore backup
            self.restore_backup_checkbox.current.disabled = False

    def on_restore_backup_toggled(self, e):
        """Show or hide backup path input based on toggle."""
        use_backup = self.restore_backup_checkbox.current.value
        if self.backup_path_container.current:
            self.backup_path_container.current.visible = use_backup
        if not use_backup and self.backup_path_field.current:
            self.backup_path_field.current.value = ""
        self.page.update()

    def on_backup_path_selected(self, e: ft.FilePickerResultEvent):
        """Handle backup path folder selection."""
        if e.path:
            self.backup_path_field.current.value = e.path
            # Auto-enable the checkbox when a path is selected
            self.restore_backup_checkbox.current.value = True
            if self.backup_path_container.current:
                self.backup_path_container.current.visible = True
            self.page.update()
            self.show_snackbar(f"Selected: {e.path}", ft.Colors.GREEN)

    def on_restore_path_selected(self, e: ft.FilePickerResultEvent):
        """Handle restore directory selection."""
        if e.path:
            self.run_command("restore", backup_dir=e.path)

    def on_step_checkbox_changed(self, key: str):
        """Ensure at least one install step stays enabled."""
        states = {
            "bootstrap": (
                self.bootstrap_checkbox.current.value
                if self.bootstrap_checkbox.current
                else False
            ),
            "k2": self.k2_checkbox.current.value if self.k2_checkbox.current else False,
            "repo": (
                self.repo_checkbox.current.value
                if self.repo_checkbox.current
                else False
            ),
        }
        if not any(states.values()):
            ref_map = {
                "bootstrap": self.bootstrap_checkbox,
                "k2": self.k2_checkbox,
                "repo": self.repo_checkbox,
            }
            ref = ref_map.get(key)
            if ref and ref.current:
                ref.current.value = True
                ref.current.update()
            self.show_snackbar(
                "Keep at least one installation step selected", ft.Colors.ORANGE
            )
            return
        if key == "repo":
            self.update_branch_visibility()
        self.page.update()

    def update_branch_visibility(self):
        """Show/hide branch dropdown based on repo checkbox."""
        show_branch = (
            self.repo_checkbox.current.value if self.repo_checkbox.current else False
        )
        if self.branch_field.current:
            self.branch_field.current.visible = show_branch
            self.branch_field.current.update()

    def toggle_log_visibility(self, e):
        """Toggle log body visibility without removing the container."""
        log_view = self.log_view.current
        container = self.log_container.current
        if not log_view or not container:
            return

        # Toggle the container that wraps the ListView
        log_view.parent.visible = not log_view.parent.visible
        self.log_visible = log_view.parent.visible

        if self.log_visible:
            self.toggle_log_btn.current.icon = ft.Icons.MINIMIZE
            self.toggle_log_btn.current.tooltip = "Hide Log"
        else:
            self.toggle_log_btn.current.icon = ft.Icons.OPEN_IN_FULL
            self.toggle_log_btn.current.tooltip = "Show Log"

        container.update()
        self.adjust_window_height()

    def show_log(self):
        """Ensure log container and field are visible, expanding window."""
        if not self.log_container.current.visible:
            self.log_container.current.visible = True
        self.log_view.current.parent.visible = True
        self.toggle_log_btn.current.icon = ft.Icons.MINIMIZE
        self.toggle_log_btn.current.tooltip = "Hide Log"
        self.log_visible = True
        self.adjust_window_height()

    def clear_log(self, e):
        """Clear the log."""
        self.log_view.current.controls.clear()
        self.full_log_buffer = ""
        self.detailed_log_buffer = ""
        self.page.update()

    def on_verbose_toggled(self, e):
        """Toggle between concise console log and verbose log."""
        # Ignore programmatic changes
        if self._programmatic_checkbox_change:
            return

        self.verbose_log = self.verbose_checkbox.current.value
        log_view = self.log_view.current
        if not log_view:
            return

        # Clear existing controls
        log_view.controls.clear()

        # Select appropriate buffer
        buffer = self.detailed_log_buffer if self.verbose_log else self.full_log_buffer

        # Split by newlines and create Text item for each line
        lines = buffer.split("\n")
        for line in lines:
            if line:  # Skip empty lines
                log_view.controls.append(
                    ft.Text(line, font_family="Courier New", size=12, selectable=True)
                )

        # Update display - ListView will auto-scroll to bottom with auto_scroll=True
        self.page.update()

    def download_log(self, e):
        """Open file picker to save log file."""
        if not self.detailed_log_buffer:
            self.show_snackbar("No log data available", ft.Colors.ORANGE)
            return

        # Suggest a filename with timestamp
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested_name = f"printer_installer_{timestamp}.log"
        self.log_save_picker.save_file(
            dialog_title="Save Full Log File",
            file_name=suggested_name,
            allowed_extensions=["log", "txt"],
        )

    def on_log_save_selected(self, e: ft.FilePickerResultEvent):
        """Handle log file save location selection."""
        if not e.path:
            return

        try:
            with open(e.path, "w", encoding="utf-8") as f:
                f.write(self.detailed_log_buffer)
            self.show_snackbar(f"Log saved to {e.path}", ft.Colors.GREEN)
        except Exception as exc:
            self.show_snackbar(f"Failed to save log: {exc}", ft.Colors.RED)

    def create_factory_reset_dialog(self):
        """Create factory reset confirmation dialog."""
        if self.factory_reset_dialog:
            return

        dialog_content = ft.Column(
            [
                ft.Text(
                    "Do you want to create a backup before running a factory reset?"
                ),
                ft.Text(
                    "Warning: there is no further confirmation.",
                    color=ft.Colors.RED_300,
                    size=12,
                ),
            ],
            spacing=6,
            tight=True,
        )
        self.factory_reset_dialog = ft.AlertDialog(
            modal=False,
            content_padding=ft.padding.symmetric(horizontal=16, vertical=12),
            actions_alignment=ft.MainAxisAlignment.END,
            title=ft.Text("Factory Reset"),
            content=dialog_content,
            actions=[
                ft.TextButton(
                    "Cancel",
                    on_click=self.dismiss_factory_reset_dialog,
                ),
                ft.TextButton(
                    "Backup & Reset",
                    on_click=lambda _: self.handle_factory_reset_choice(True),
                ),
                ft.TextButton(
                    "Reset Only",
                    on_click=lambda _: self.handle_factory_reset_choice(False),
                ),
            ],
            on_dismiss=self.dismiss_factory_reset_dialog,
        )
        self.page.overlay.append(self.factory_reset_dialog)

    def show_factory_reset_dialog(self, e=None):
        """Prompt user to choose factory reset workflow."""
        self.create_factory_reset_dialog()
        if self.factory_reset_dialog:
            self.factory_reset_dialog.open = True
            self.page.update()

    def dismiss_factory_reset_dialog(self, e=None):
        """Close the factory reset dialog without action."""
        if self.factory_reset_dialog:
            self.factory_reset_dialog.open = False
            self.page.update()

    def handle_factory_reset_choice(self, with_backup: bool):
        """Execute factory reset according to user choice."""
        self.dismiss_factory_reset_dialog()
        self.run_command("reset_only", with_backup=with_backup)

    def create_backup_dialog(self):
        """Create backup location selection dialog."""
        if self.backup_dialog:
            return

        dialog_content = ft.Column(
            [
                ft.Text("Select where to save the Moonraker stats backup."),
                ft.Row(
                    [
                        ft.TextField(
                            ref=self.backup_dialog_path_field,
                            label="Backup Directory",
                            hint_text="/path/to/backup",
                            value="",
                            dense=True,
                            expand=1,
                        ),
                        ft.FilledButton(
                            text="Browse",
                            on_click=self.on_backup_dialog_browse_click,
                            height=36,
                        ),
                    ],
                    spacing=8,
                ),
            ],
            spacing=12,
            tight=True,
        )

        self.backup_dialog = ft.AlertDialog(
            modal=False,
            content_padding=ft.padding.symmetric(horizontal=16, vertical=12),
            actions_alignment=ft.MainAxisAlignment.END,
            title=ft.Text("Backup Moonraker Stats"),
            content=dialog_content,
            actions=[
                ft.TextButton(
                    "Cancel",
                    on_click=self.dismiss_backup_dialog,
                ),
                ft.TextButton(
                    "Start Backup",
                    on_click=self.handle_backup_dialog_confirm,
                ),
            ],
            on_dismiss=self.dismiss_backup_dialog,
        )
        self.page.overlay.append(self.backup_dialog)

    def show_backup_dialog(self, e=None):
        """Show backup location selection dialog."""
        self.create_backup_dialog()
        if self.backup_dialog:
            # Clear previous path
            if self.backup_dialog_path_field.current:
                self.backup_dialog_path_field.current.value = ""
            self.backup_dialog.open = True
            self.page.update()

    def dismiss_backup_dialog(self, e=None):
        """Close backup dialog without action."""
        if self.backup_dialog:
            self.backup_dialog.open = False
            self.page.update()

    def on_backup_dialog_browse_click(self, e):
        """Open directory picker for backup dialog."""
        self.backup_save_picker.get_directory_path(
            dialog_title="Select Backup Directory", initial_directory=self.current_dir
        )

    def handle_backup_dialog_confirm(self, e):
        """Execute backup with selected directory."""
        backup_path = (
            self.backup_dialog_path_field.current.value.strip()
            if self.backup_dialog_path_field.current
            else ""
        )
        if not backup_path:
            self.show_snackbar("Please select a backup directory", ft.Colors.ORANGE)
            return

        self.dismiss_backup_dialog()
        self.run_command("backup", backup_dir=backup_path)

    def quick_restore(self):
        """Quick restore action."""
        self.restore_picker.get_directory_path(
            dialog_title="Select Backup Directory", initial_directory=self.current_dir
        )

    def on_backup_save_selected(self, e: ft.FilePickerResultEvent):
        """Handle backup directory selection from picker."""
        if e.path:
            # Update the dialog text field
            if self.backup_dialog_path_field.current:
                self.backup_dialog_path_field.current.value = e.path
                self.page.update()

    def on_browse_button_click(self, e):
        """Handle browse button click - always enabled."""
        self.backup_path_picker.get_directory_path(
            dialog_title="Select Backup Directory", initial_directory=self.current_dir
        )

    def strip_ansi_codes(self, text: str) -> str:
        """Remove ANSI color codes from text."""
        ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
        return ansi_escape.sub("", text)

    def build_command(self, action: str, **kwargs) -> list[str] | None:
        """Build CLI arguments for fullinstaller."""
        ip = self.ip_field.current.value.strip()

        if not ip:
            self.show_snackbar("Please enter a printer IP", ft.Colors.ORANGE)
            return None
        args: list[str] = [ip]

        if action == "ssh_key":
            args.append("--key-only")
        elif action == "backup":
            backup_dir = kwargs.get("backup_dir", "")
            if backup_dir:
                args.extend(["--backup-only", backup_dir])
            else:
                self.show_snackbar("Please select a backup directory", ft.Colors.ORANGE)
                return None
        elif action == "restore":
            backup_dir = kwargs.get("backup_dir", "")
            if backup_dir:
                args.extend(["--restore-only", backup_dir])
            else:
                self.show_snackbar("Please select a backup directory", ft.Colors.ORANGE)
                return None
        elif action == "reset_only":
            args.append("--reset-only")
            if kwargs.get("with_backup"):
                args.append("--backup")
        elif action == "install":
            bootstrap_selected = (
                self.bootstrap_checkbox.current.value
                if self.bootstrap_checkbox.current
                else False
            )
            k2_selected = (
                self.k2_checkbox.current.value if self.k2_checkbox.current else False
            )
            repo_selected = (
                self.repo_checkbox.current.value
                if self.repo_checkbox.current
                else False
            )
            if not any([bootstrap_selected, k2_selected, repo_selected]):
                self.show_snackbar(
                    "Select at least one installation step", ft.Colors.ORANGE
                )
                return None
            branch_value = (
                self.branch_field.current.value
                if (self.branch_field.current and repo_selected)
                else None
            )
            branch = branch_value.strip() if isinstance(branch_value, str) else ""
            branch = branch or "main"
            if repo_selected:
                args.append(branch)
            if self.reset_checkbox.current.value:
                args.append("--reset")
            if self.preserve_stats_checkbox.current.value:
                args.append("--preserve-stats")
            if self.restore_backup_checkbox.current.value:
                backup_path = self.backup_path_field.current.value
                if backup_path:
                    args.extend(["--restore-backup", backup_path])
            if bootstrap_selected:
                args.append("--run-bootstrap")
            if k2_selected:
                args.append("--run-k2")
            if repo_selected:
                args.append("--run-repo")

        return args

    def get_action_label(self, action: str) -> str:
        """Get friendly label for action."""
        labels = {
            "ssh_key": "Installing SSH Key",
            "backup": "Backing Up",
            "restore": "Restoring",
            "reset_only": "Factory Resetting",
            "install": "Installing",
        }
        return labels.get(action, "Running")

    def run_command(self, action: str, **kwargs):
        """Run the installer command in a background process."""
        if self.worker_process and self.worker_process.is_alive():
            self.show_snackbar("An operation is already running", ft.Colors.ORANGE)
            return

        args = self.build_command(action, **kwargs)
        if not args:
            return

        self.cancel_event = threading.Event()
        self.log_view.current.controls.clear()
        self.full_log_buffer = ""
        self.detailed_log_buffer = ""
        self.verbose_log = False
        # Show download button when installation starts
        self.download_log_btn.current.visible = True
        # Make verbose checkbox always visible (can be toggled anytime)
        self.verbose_checkbox.current.visible = True
        # Set flag before changing value to prevent on_change from firing
        self._programmatic_checkbox_change = True
        self.verbose_checkbox.current.value = False
        self._programmatic_checkbox_change = False
        action_label = self.get_action_label(action)
        self.update_button_status(f"{action_label}...", ft.Colors.ORANGE_700, True)
        self.cancel_btn.current.visible = True

        # Show log automatically when running command
        self.show_log()

        self.page.update()

        self.worker_queue = mp.Queue()
        self.worker_process = mp.Process(
            target=installer_worker.run_installation,
            args=(args, self.worker_queue),
            daemon=True,
        )
        self.worker_process.start()

        self.worker_listener = threading.Thread(
            target=self._monitor_worker, args=(action,), daemon=True
        )
        self.worker_listener.start()

    def append_log(self, text: str):
        """Append text to the log view."""
        if not text:
            return
        log_view = self.log_view.current
        if not log_view:
            return
        clean_text = self.strip_ansi_codes(text)

        # Check if this is a verbose-only message
        is_verbose_only = "[VERBOSE]" in clean_text

        # Store in detailed buffer (everything)
        self.detailed_log_buffer += clean_text

        # Store in concise buffer (only console messages, not verbose-only)
        if not is_verbose_only:
            self.full_log_buffer += clean_text

        # Update visible log display based on current verbose_log mode
        if not self.verbose_log:
            # In concise mode, only show console messages (not verbose-only)
            if not is_verbose_only:
                # Split by newlines and add each line as a Text item
                lines = clean_text.split("\n")
                for line in lines:
                    if line:  # Skip empty lines
                        log_view.controls.append(
                            ft.Text(
                                line,
                                font_family="Courier New",
                                size=12,
                                selectable=True,
                            )
                        )
        else:
            # In verbose mode, show everything from detailed buffer
            # Split by newlines and add each line as a Text item
            lines = clean_text.split("\n")
            for line in lines:
                if line:  # Skip empty lines
                    log_view.controls.append(
                        ft.Text(
                            line, font_family="Courier New", size=12, selectable=True
                        )
                    )

        self.page.update()

    def _monitor_worker(self, action: str):
        """Consume worker messages and finalize UI state."""
        exit_code: int | None = None
        try:
            while True:
                try:
                    message = self.worker_queue.get(timeout=0.1)
                except queue.Empty:
                    if self.worker_process and not self.worker_process.is_alive():
                        break
                    continue

                if not isinstance(message, dict):
                    continue

                msg_type = message.get("type")
                if msg_type == "log":
                    self.append_log(message.get("data", ""))
                elif msg_type == "exit":
                    exit_code = message.get("code", 1)
                    if message.get("error"):
                        self.append_log(message["error"] + "\n")
                    break
        finally:
            if self.worker_process:
                self.worker_process.join(timeout=1)
            if self.worker_queue:
                with contextlib.suppress(Exception):
                    self.worker_queue.close()
                with contextlib.suppress(Exception):
                    self.worker_queue.join_thread()

            self.worker_process = None
            self.worker_queue = None
            self.worker_listener = None

            self._finalize_worker(action, exit_code)

    def _finalize_worker(self, action: str, exit_code: int | None):
        """Update UI after worker completion."""
        self.cancel_btn.current.visible = False

        if self.cancel_event and self.cancel_event.is_set():
            self.update_button_status("Cancelled", ft.Colors.ORANGE_700, True)
            self.show_snackbar("Installation cancelled", ft.Colors.ORANGE)
        elif exit_code == 0:
            self.update_button_status("Complete! ✓", ft.Colors.GREEN_700, True)
            self.show_snackbar(f"{action.title()} successful!", ft.Colors.GREEN)
        else:
            self.update_button_status("Failed ✗", ft.Colors.RED_700, True)
            self.show_snackbar(f"{action.title()} failed!", ft.Colors.RED)

        threading.Timer(
            2.0,
            lambda: self.update_button_status(
                "Start Installation", ft.Colors.BLUE, False
            ),
        ).start()

        self.cancel_event = None
        self.page.update()

    def cancel_installation(self, e):
        """Cancel running process."""
        if self.worker_process and self.worker_process.is_alive():
            if not self.cancel_event:
                self.cancel_event = threading.Event()
            self.cancel_event.set()
            self.worker_process.terminate()
            self.show_snackbar("Cancelling...", ft.Colors.ORANGE)
        else:
            self.show_snackbar("No active operation to cancel", ft.Colors.ORANGE)


def main(page: ft.Page):
    PrinterInstallerGUI(page)


if __name__ == "__main__":
    mp.freeze_support()
    ft.app(target=main)

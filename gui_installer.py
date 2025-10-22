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
import time
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

        # Backup sizes cache
        self.backup_sizes_cache: dict[str, dict] | None = None
        self.CACHE_EXPIRY_SECONDS = 300  # 5 minutes

        # Refs
        self.ip_field = ft.Ref[ft.TextField]()
        self.branch_field = ft.Ref[ft.Dropdown]()
        self.reset_checkbox = ft.Ref[ft.Checkbox]()
        self.preserve_stats_checkbox = ft.Ref[ft.Checkbox]()
        self.preserve_timelapses_checkbox = ft.Ref[ft.Checkbox]()
        self.preserve_gcodes_checkbox = ft.Ref[ft.Checkbox]()
        self.preserve_stats_loading = ft.Ref[ft.ProgressRing]()
        self.preserve_timelapses_loading = ft.Ref[ft.ProgressRing]()
        self.preserve_gcodes_loading = ft.Ref[ft.ProgressRing]()
        self.preserve_stats_size_text = ft.Ref[ft.Text]()
        self.preserve_timelapses_size_text = ft.Ref[ft.Text]()
        self.preserve_gcodes_size_text = ft.Ref[ft.Text]()
        self.bootstrap_checkbox = ft.Ref[ft.Checkbox]()
        self.k2_checkbox = ft.Ref[ft.Checkbox]()
        self.repo_checkbox = ft.Ref[ft.Checkbox]()
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
        self.backup_dialog_moonraker_checkbox = ft.Ref[ft.Checkbox]()
        self.backup_dialog_timelapses_checkbox = ft.Ref[ft.Checkbox]()
        self.backup_dialog_gcodes_checkbox = ft.Ref[ft.Checkbox]()
        self.backup_dialog_moonraker_size_text = ft.Ref[ft.Text]()
        self.backup_dialog_timelapses_size_text = ft.Ref[ft.Text]()
        self.backup_dialog_gcodes_size_text = ft.Ref[ft.Text]()
        self.backup_dialog_moonraker_loading = ft.Ref[ft.ProgressRing]()
        self.backup_dialog_timelapses_loading = ft.Ref[ft.ProgressRing]()
        self.backup_dialog_gcodes_loading = ft.Ref[ft.ProgressRing]()
        self.backup_dialog_timelapses_row = ft.Ref[ft.Row]()
        self.backup_dialog_gcodes_row = ft.Ref[ft.Row]()
        self.backup_dialog_start_btn = ft.Ref[ft.TextButton]()
        self.restore_dialog = None
        self.restore_dialog_moonraker_checkbox = ft.Ref[ft.Checkbox]()
        self.restore_dialog_timelapses_checkbox = ft.Ref[ft.Checkbox]()
        self.restore_dialog_gcodes_checkbox = ft.Ref[ft.Checkbox]()
        self.restore_dialog_moonraker_size_text = ft.Ref[ft.Text]()
        self.restore_dialog_timelapses_size_text = ft.Ref[ft.Text]()
        self.restore_dialog_gcodes_size_text = ft.Ref[ft.Text]()
        self.restore_dialog_checkboxes_container = ft.Ref[ft.Container]()
        self.restore_dialog_start_btn = ft.Ref[ft.TextButton]()
        self.restore_dialog_path_field = ft.Ref[ft.TextField]()
        self.create_factory_reset_dialog()
        self.create_backup_dialog()

        # File pickers
        self.backup_save_picker = ft.FilePicker(on_result=self.on_backup_save_selected)
        self.restore_picker = ft.FilePicker(on_result=self.on_restore_path_selected)
        self.log_save_picker = ft.FilePicker(on_result=self.on_log_save_selected)
        self.page.overlay.append(self.backup_save_picker)
        self.page.overlay.append(self.restore_picker)
        self.page.overlay.append(self.log_save_picker)

        self.configure_multiprocessing()

        self.setup_ui()
        self.update_button_status("Start Installation", ft.Colors.BLUE, False)
        
        # Trigger macOS LAN permission prompt early by attempting local network access
        threading.Thread(target=self.trigger_lan_permission_prompt, daemon=True).start()
        
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
                                "💾 Backup",
                                on_click=lambda _: self.show_backup_dialog(),
                                height=36,
                            ),
                            ft.ElevatedButton(
                                "↩️ Restore",
                                on_click=lambda _: self.show_restore_dialog(),
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
                                                        content=ft.Row(
                                                            [
                                                                ft.Checkbox(
                                                                    ref=self.preserve_stats_checkbox,
                                                                    label="↳ Preserve Moonraker Stats",
                                                                    value=False,
                                                                    disabled=True,
                                                                    label_style=ft.TextStyle(
                                                                        color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                                                                    ),
                                                                    on_change=self.on_preserve_stats_toggled,
                                                                ),
                                                                ft.ProgressRing(
                                                                    ref=self.preserve_stats_loading,
                                                                    width=16,
                                                                    height=16,
                                                                    stroke_width=2,
                                                                    visible=False,
                                                                ),
                                                                ft.Text(
                                                                    ref=self.preserve_stats_size_text,
                                                                    size=11,
                                                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                                                ),
                                                            ],
                                                            spacing=8,
                                                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                        ),
                                                        padding=ft.padding.only(
                                                            left=28
                                                        ),
                                                    ),
                                                    height=40,
                                                ),
                                                ft.Container(
                                                    content=ft.Container(
                                                        content=ft.Row(
                                                            [
                                                                ft.Checkbox(
                                                                    ref=self.preserve_timelapses_checkbox,
                                                                    label="↳ Preserve Timelapses",
                                                                    value=False,
                                                                    disabled=True,
                                                                    label_style=ft.TextStyle(
                                                                        color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                                                                    ),
                                                                    on_change=self.on_preserve_timelapses_toggled,
                                                                ),
                                                                ft.ProgressRing(
                                                                    ref=self.preserve_timelapses_loading,
                                                                    width=16,
                                                                    height=16,
                                                                    stroke_width=2,
                                                                    visible=False,
                                                                ),
                                                                ft.Text(
                                                                    ref=self.preserve_timelapses_size_text,
                                                                    size=11,
                                                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                                                ),
                                                            ],
                                                            spacing=8,
                                                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                        ),
                                                        padding=ft.padding.only(
                                                            left=28
                                                        ),
                                                    ),
                                                    height=40,
                                                ),
                                                ft.Container(
                                                    content=ft.Container(
                                                        content=ft.Row(
                                                            [
                                                                ft.Checkbox(
                                                                    ref=self.preserve_gcodes_checkbox,
                                                                    label="↳ Preserve GCodes",
                                                                    value=False,
                                                                    disabled=True,
                                                                    label_style=ft.TextStyle(
                                                                        color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                                                                    ),
                                                                    on_change=self.on_preserve_gcodes_toggled,
                                                                ),
                                                                ft.ProgressRing(
                                                                    ref=self.preserve_gcodes_loading,
                                                                    width=16,
                                                                    height=16,
                                                                    stroke_width=2,
                                                                    visible=False,
                                                                ),
                                                                ft.Text(
                                                                    ref=self.preserve_gcodes_size_text,
                                                                    size=11,
                                                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                                                ),
                                                            ],
                                                            spacing=8,
                                                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                        ),
                                                        padding=ft.padding.only(
                                                            left=28
                                                        ),
                                                    ),
                                                    height=40,
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

    def trigger_lan_permission_prompt(self):
        """Trigger macOS Local Network permission prompt on startup."""
        import socket
        
        if sys.platform != "darwin":
            return
        
        ip = self.ip_field.current.value.strip() if self.ip_field.current else ""
        if not ip:
            return
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect((ip, 22))
            sock.close()
        except (socket.timeout, socket.error, OSError):
            pass

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
        """Toggle preserve stats and timelapses checkboxes."""
        is_reset_enabled = self.reset_checkbox.current.value
        
        if not is_reset_enabled:
            # Disable and clear checkboxes
            self.preserve_stats_checkbox.current.disabled = True
            self.preserve_timelapses_checkbox.current.disabled = True
            self.preserve_stats_checkbox.current.value = False
            self.preserve_timelapses_checkbox.current.value = False
            # Apply greyed-out label style when disabled
            if self.preserve_stats_checkbox.current:
                self.preserve_stats_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
            if self.preserve_timelapses_checkbox.current:
                self.preserve_timelapses_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
            if self.preserve_gcodes_checkbox.current:
                self.preserve_gcodes_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
            # Hide any loading indicators
            if self.preserve_stats_loading.current:
                self.preserve_stats_loading.current.visible = False
            if self.preserve_timelapses_loading.current:
                self.preserve_timelapses_loading.current.visible = False
            if self.preserve_gcodes_loading.current:
                self.preserve_gcodes_loading.current.visible = False
            # Clear size text
            if self.preserve_stats_size_text.current:
                self.preserve_stats_size_text.current.value = ""
            if self.preserve_timelapses_size_text.current:
                self.preserve_timelapses_size_text.current.value = ""
            if self.preserve_gcodes_size_text.current:
                self.preserve_gcodes_size_text.current.value = ""
            self.page.update()
            return
        
        # Factory reset is being enabled - check cache
        ip = self.ip_field.current.value.strip() if self.ip_field.current else ""
        
        if not ip:
            # No IP - just enable all checkboxes
            self.preserve_stats_checkbox.current.disabled = False
            self.preserve_timelapses_checkbox.current.disabled = False
            self.preserve_gcodes_checkbox.current.disabled = False
            self.page.update()
            return
        
        # Check cache synchronously
        cache_valid = False
        if self.backup_sizes_cache is not None:
            cache = self.backup_sizes_cache
            cache_ip = cache.get("ip")
            cache_timestamp = cache.get("timestamp", 0)
            current_time = time.time()
            time_diff = current_time - cache_timestamp
            
            print(f"[MAIN_MENU_CACHE] Checking cache for IP: {ip}")
            print(f"[MAIN_MENU_CACHE] Cache IP: {cache_ip}, Time diff: {time_diff}s")
            
            if cache_ip == ip and time_diff < self.CACHE_EXPIRY_SECONDS:
                cache_valid = True
                print(f"[MAIN_MENU_CACHE] Cache VALID - using cached data")
                sizes = cache.get("sizes", {})
                self._update_main_menu_with_sizes(sizes)
            else:
                print(f"[MAIN_MENU_CACHE] Cache INVALID - will query")
        else:
            print(f"[MAIN_MENU_CACHE] No cache exists")
        
        if not cache_valid:
            # Cache invalid - enable all checkboxes and show loading
            self.preserve_stats_checkbox.current.disabled = False
            self.preserve_timelapses_checkbox.current.disabled = False
            self.preserve_gcodes_checkbox.current.disabled = False
            
            # Show loading indicators
            if self.preserve_stats_loading.current:
                self.preserve_stats_loading.current.visible = True
            if self.preserve_timelapses_loading.current:
                self.preserve_timelapses_loading.current.visible = True
            if self.preserve_gcodes_loading.current:
                self.preserve_gcodes_loading.current.visible = True
            
            self.page.update()
            
            # Start background query
            print(f"[MAIN_MENU_CACHE] Starting background query thread")
            threading.Thread(target=self.query_sizes_for_main_menu, daemon=True).start()
        else:
            # Cache valid - checkboxes already updated
            self.page.update()

    def on_preserve_stats_toggled(self, e):
        """Handle preserve stats toggle."""
        self.page.update()

    def on_preserve_timelapses_toggled(self, e):
        """Handle preserve timelapses toggle."""
        self.page.update()

    def on_preserve_gcodes_toggled(self, e):
        """Handle preserve gcodes toggle."""
        self.page.update()

    def _update_main_menu_with_sizes(self, sizes: dict[str, dict]):
        """Update main menu checkbox states based on size data."""
        moonraker_info = sizes.get("moonraker", {})
        timelapses_info = sizes.get("timelapses", {})
        gcodes_info = sizes.get("gcodes", {})
        
        moonraker_exists = moonraker_info.get("exists", True)  # Default to True (optimistic)
        timelapses_exist = timelapses_info.get("exists", True)  # Default to True (optimistic)
        gcodes_exist = gcodes_info.get("exists", True)  # Default to True (optimistic)
        
        print(f"[MAIN_MENU_CACHE] Updating UI - moonraker exists: {moonraker_exists}, timelapses exist: {timelapses_exist}, gcodes exist: {gcodes_exist}")
        
        # Update Moonraker checkbox and size display
        if self.preserve_stats_checkbox.current:
            self.preserve_stats_checkbox.current.disabled = not moonraker_exists
            if not moonraker_exists:
                self.preserve_stats_checkbox.current.value = False
                # Make label text more greyed out when disabled
                self.preserve_stats_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
            else:
                # Reset to default label color when enabled
                self.preserve_stats_checkbox.current.label_style = None
        
        if self.preserve_stats_size_text.current:
            if moonraker_exists:
                count = moonraker_info.get("count", 0)
                size_kb = moonraker_info.get("size_kb", 0)
                size_str = self.format_size(size_kb)
                self.preserve_stats_size_text.current.value = f"({count} file{'s' if count != 1 else ''}, {size_str})"
            else:
                self.preserve_stats_size_text.current.value = "(None detected)"
        
        # Update Timelapses checkbox and size display
        if self.preserve_timelapses_checkbox.current:
            self.preserve_timelapses_checkbox.current.disabled = not timelapses_exist
            if not timelapses_exist:
                self.preserve_timelapses_checkbox.current.value = False
                # Make label text more greyed out when disabled
                self.preserve_timelapses_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
            else:
                # Reset to default label color when enabled
                self.preserve_timelapses_checkbox.current.label_style = None
        
        if self.preserve_timelapses_size_text.current:
            if timelapses_exist:
                count = timelapses_info.get("count", 0)
                size_kb = timelapses_info.get("size_kb", 0)
                size_str = self.format_size(size_kb)
                self.preserve_timelapses_size_text.current.value = f"({count} file{'s' if count != 1 else ''}, {size_str})"
            else:
                self.preserve_timelapses_size_text.current.value = "(None detected)"
        
        # Update GCodes checkbox and size display
        if self.preserve_gcodes_checkbox.current:
            self.preserve_gcodes_checkbox.current.disabled = not gcodes_exist
            if not gcodes_exist:
                self.preserve_gcodes_checkbox.current.value = False
                # Make label text more greyed out when disabled
                self.preserve_gcodes_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
            else:
                # Reset to default label color when enabled
                self.preserve_gcodes_checkbox.current.label_style = None
        
        if self.preserve_gcodes_size_text.current:
            if gcodes_exist:
                count = gcodes_info.get("count", 0)
                size_kb = gcodes_info.get("size_kb", 0)
                size_str = self.format_size(size_kb)
                self.preserve_gcodes_size_text.current.value = f"({count} file{'s' if count != 1 else ''}, {size_str})"
            else:
                self.preserve_gcodes_size_text.current.value = "(None detected)"
        
        # Hide loading indicators
        if self.preserve_stats_loading.current:
            self.preserve_stats_loading.current.visible = False
        if self.preserve_timelapses_loading.current:
            self.preserve_timelapses_loading.current.visible = False
        if self.preserve_gcodes_loading.current:
            self.preserve_gcodes_loading.current.visible = False

    def query_sizes_for_main_menu(self):
        """Query backup sizes for main menu checkboxes (background thread)."""
        ip = self.ip_field.current.value.strip() if self.ip_field.current else ""
        if not ip:
            return
        
        print(f"[MAIN_MENU_CACHE] Background query starting for IP: {ip}")
        
        # Reuse the existing query logic
        try:
            from fullinstaller import PrinterInstaller, SSHConnectionError
            import logging
            
            temp_installer = PrinterInstaller(
                printer_ip=ip,
                branch="main",
                password="creality_2024",
            )
            temp_installer.logger.setLevel(logging.CRITICAL)
            temp_installer.logger.handlers.clear()
            
            sizes = temp_installer.query_backup_sizes()
            temp_installer.executor.close()
            
            print(f"[MAIN_MENU_CACHE] Query completed successfully")
            
            # Update cache
            self.backup_sizes_cache = {
                "ip": ip,
                "timestamp": time.time(),
                "sizes": sizes,
            }
            
            # Update UI
            self._update_main_menu_with_sizes(sizes)
            
        except Exception as exc:
            print(f"[MAIN_MENU_CACHE] Query failed: {type(exc).__name__}: {exc}")
            # On failure, just enable all checkboxes (optimistic)
            if self.preserve_stats_checkbox.current:
                self.preserve_stats_checkbox.current.disabled = False
            if self.preserve_timelapses_checkbox.current:
                self.preserve_timelapses_checkbox.current.disabled = False
            if self.preserve_gcodes_checkbox.current:
                self.preserve_gcodes_checkbox.current.disabled = False
        
        # Hide loading indicators
        if self.preserve_stats_loading.current:
            self.preserve_stats_loading.current.visible = False
        if self.preserve_timelapses_loading.current:
            self.preserve_timelapses_loading.current.visible = False
        if self.preserve_gcodes_loading.current:
            self.preserve_gcodes_loading.current.visible = False
        
        self.page.update()

    def on_restore_path_selected(self, e: ft.FilePickerResultEvent):
        """Handle restore directory selection from picker."""
        if e.path:
            self.restore_dialog_path_field.current.value = e.path
            self.on_restore_path_changed(None)  # Trigger validation

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
        """Create backup dialog with component selection."""
        if self.backup_dialog:
            return

        dialog_content = ft.Column(
            [
                ft.Text("Select what to backup and where to save it."),
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
                ft.Divider(),
                ft.Text("Components to backup:", size=13, weight=ft.FontWeight.W_500),
                ft.Row(
                    [
                        ft.Checkbox(
                            ref=self.backup_dialog_moonraker_checkbox,
                            label="Moonraker Stats",
                            value=True,
                            on_change=self.on_backup_component_changed,
                        ),
                        ft.ProgressRing(
                            ref=self.backup_dialog_moonraker_loading,
                            width=16,
                            height=16,
                            stroke_width=2,
                            visible=False,
                        ),
                        ft.Text(
                            ref=self.backup_dialog_moonraker_size_text,
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        ft.Checkbox(
                            ref=self.backup_dialog_timelapses_checkbox,
                            label="Timelapses",
                            value=True,
                            on_change=self.on_backup_component_changed,
                        ),
                        ft.ProgressRing(
                            ref=self.backup_dialog_timelapses_loading,
                            width=16,
                            height=16,
                            stroke_width=2,
                            visible=False,
                        ),
                        ft.Text(
                            ref=self.backup_dialog_timelapses_size_text,
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    ref=self.backup_dialog_timelapses_row,
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        ft.Checkbox(
                            ref=self.backup_dialog_gcodes_checkbox,
                            label="GCodes",
                            value=True,
                            on_change=self.on_backup_component_changed,
                        ),
                        ft.ProgressRing(
                            ref=self.backup_dialog_gcodes_loading,
                            width=16,
                            height=16,
                            stroke_width=2,
                            visible=False,
                        ),
                        ft.Text(
                            ref=self.backup_dialog_gcodes_size_text,
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    ref=self.backup_dialog_gcodes_row,
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=12,
            tight=True,
        )

        self.backup_dialog = ft.AlertDialog(
            modal=False,
            content_padding=ft.padding.symmetric(horizontal=16, vertical=12),
            actions_alignment=ft.MainAxisAlignment.END,
            title=ft.Text("Backup"),
            content=dialog_content,
            actions=[
                ft.TextButton(
                    "Cancel",
                    on_click=self.dismiss_backup_dialog,
                ),
                ft.TextButton(
                    ref=self.backup_dialog_start_btn,
                    text="Start Backup",
                    on_click=self.handle_backup_dialog_confirm,
                ),
            ],
            on_dismiss=self.dismiss_backup_dialog,
        )
        self.page.overlay.append(self.backup_dialog)

    def format_size(self, size_kb: int) -> str:
        """Convert kilobytes to human-readable format."""
        if size_kb == 0:
            return "0 MB"
        elif size_kb < 1024:
            return f"{size_kb} KB"
        elif size_kb < 1024 * 1024:
            mb = size_kb / 1024
            return f"{mb:.1f} MB" if mb < 10 else f"{mb:.0f} MB"
        else:
            gb = size_kb / (1024 * 1024)
            return f"{gb:.2f} GB" if gb < 10 else f"{gb:.1f} GB"

    def _update_ui_with_sizes(self, sizes: dict[str, dict[str, int | bool]], query_successful: bool = False):
        """Update UI with size information.
        
        Args:
            sizes: Size information dict
            query_successful: If True, disable checkboxes and show "None detected" if components don't exist
        """
        moonraker_info = sizes.get("moonraker", {})
        timelapses_info = sizes.get("timelapses", {})
        gcodes_info = sizes.get("gcodes", {})
        
        # Update Moonraker display
        moonraker_exists = moonraker_info.get("exists", False)
        if moonraker_exists:
            count = moonraker_info.get("count", 0)
            size_kb = moonraker_info.get("size_kb", 0)
            size_str = self.format_size(size_kb)
            if self.backup_dialog_moonraker_size_text.current:
                self.backup_dialog_moonraker_size_text.current.value = f"{count} file{'s' if count != 1 else ''}, {size_str}"
            # Enable checkbox if moonraker exists
            if self.backup_dialog_moonraker_checkbox.current:
                self.backup_dialog_moonraker_checkbox.current.disabled = False
                self.backup_dialog_moonraker_checkbox.current.label_style = None
        else:
            # Show "None detected" if query was successful and confirmed no moonraker exists
            if query_successful and self.backup_dialog_moonraker_size_text.current:
                self.backup_dialog_moonraker_size_text.current.value = "None detected"
            elif not query_successful and self.backup_dialog_moonraker_size_text.current:
                self.backup_dialog_moonraker_size_text.current.value = ""
            # Disable checkbox if moonraker doesn't exist (but only if query was successful)
            if query_successful and self.backup_dialog_moonraker_checkbox.current:
                self.backup_dialog_moonraker_checkbox.current.disabled = True
                self.backup_dialog_moonraker_checkbox.current.value = False
                self.backup_dialog_moonraker_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
        
        # Update Timelapses display
        timelapses_exist = timelapses_info.get("exists", False)
        if timelapses_exist:
            count = timelapses_info.get("count", 0)
            size_kb = timelapses_info.get("size_kb", 0)
            size_str = self.format_size(size_kb)
            if self.backup_dialog_timelapses_size_text.current:
                self.backup_dialog_timelapses_size_text.current.value = f"{count} file{'s' if count != 1 else ''}, {size_str}"
            # Enable checkbox if timelapses exist
            if self.backup_dialog_timelapses_checkbox.current:
                self.backup_dialog_timelapses_checkbox.current.disabled = False
                self.backup_dialog_timelapses_checkbox.current.label_style = None
        else:
            # Show "None detected" if query was successful and confirmed no timelapses exist
            if query_successful and self.backup_dialog_timelapses_size_text.current:
                self.backup_dialog_timelapses_size_text.current.value = "None detected"
            elif not query_successful and self.backup_dialog_timelapses_size_text.current:
                self.backup_dialog_timelapses_size_text.current.value = ""
            # Disable checkbox if timelapses don't exist (but only if query was successful)
            if query_successful and self.backup_dialog_timelapses_checkbox.current:
                self.backup_dialog_timelapses_checkbox.current.disabled = True
                self.backup_dialog_timelapses_checkbox.current.value = False
                self.backup_dialog_timelapses_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
        
        # Update GCodes display
        gcodes_exist = gcodes_info.get("exists", False)
        if gcodes_exist:
            count = gcodes_info.get("count", 0)
            size_kb = gcodes_info.get("size_kb", 0)
            size_str = self.format_size(size_kb)
            if self.backup_dialog_gcodes_size_text.current:
                self.backup_dialog_gcodes_size_text.current.value = f"{count} file{'s' if count != 1 else ''}, {size_str}"
            # Enable checkbox if gcodes exist
            if self.backup_dialog_gcodes_checkbox.current:
                self.backup_dialog_gcodes_checkbox.current.disabled = False
                self.backup_dialog_gcodes_checkbox.current.label_style = None
        else:
            # Show "None detected" if query was successful and confirmed no gcodes exist
            if query_successful and self.backup_dialog_gcodes_size_text.current:
                self.backup_dialog_gcodes_size_text.current.value = "None detected"
            elif not query_successful and self.backup_dialog_gcodes_size_text.current:
                self.backup_dialog_gcodes_size_text.current.value = ""
            # Disable checkbox if gcodes don't exist (but only if query was successful)
            if query_successful and self.backup_dialog_gcodes_checkbox.current:
                self.backup_dialog_gcodes_checkbox.current.disabled = True
                self.backup_dialog_gcodes_checkbox.current.value = False
                self.backup_dialog_gcodes_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )

    def query_backup_sizes_async(self):
        """Query backup sizes in background thread."""
        ip = self.ip_field.current.value.strip() if self.ip_field.current else ""
        if not ip:
            return
        
        # Check cache first
        if self.backup_sizes_cache is not None:
            cache = self.backup_sizes_cache
            cache_ip = cache.get("ip")
            cache_timestamp = cache.get("timestamp", 0)
            current_time = time.time()
            time_diff = current_time - cache_timestamp
            
            # Debug logging
            print(f"[BACKUP_CACHE_DEBUG_THREAD] Thread checking cache for IP: {ip}")
            print(f"[BACKUP_CACHE_DEBUG_THREAD] Cache IP: {cache_ip}, Cache timestamp: {cache_timestamp}")
            print(f"[BACKUP_CACHE_DEBUG_THREAD] Current time: {current_time}, Time diff: {time_diff}s, Expiry: {self.CACHE_EXPIRY_SECONDS}s")
            
            # Check if IP matches and cache hasn't expired
            if cache_ip == ip and time_diff < self.CACHE_EXPIRY_SECONDS:
                print(f"[BACKUP_CACHE_DEBUG_THREAD] Cache VALID in thread - using cached data")
                # Use cached data - ensure loading indicators are hidden
                if self.backup_dialog_moonraker_loading.current:
                    self.backup_dialog_moonraker_loading.current.visible = False
                if self.backup_dialog_timelapses_loading.current:
                    self.backup_dialog_timelapses_loading.current.visible = False
                if self.backup_dialog_gcodes_loading.current:
                    self.backup_dialog_gcodes_loading.current.visible = False
                
                # Cache is validated, so we can trust it - hide timelapse if it doesn't exist
                sizes = cache.get("sizes", {})
                self._update_ui_with_sizes(sizes, query_successful=True)
                self.page.update()
                return
            else:
                print(f"[BACKUP_CACHE_DEBUG_THREAD] Cache INVALID in thread - IP match: {cache_ip == ip}, Expired: {time_diff >= self.CACHE_EXPIRY_SECONDS}")
        
        # Cache miss or invalid - query from printer
        # Show loading indicators
        if self.backup_dialog_moonraker_loading.current:
            self.backup_dialog_moonraker_loading.current.visible = True
        if self.backup_dialog_timelapses_loading.current:
            self.backup_dialog_timelapses_loading.current.visible = True
        if self.backup_dialog_gcodes_loading.current:
            self.backup_dialog_gcodes_loading.current.visible = True
        self.page.update()
        
        try:
            # Import here to avoid circular imports
            from fullinstaller import PrinterInstaller, SSHConnectionError
            import logging
            
            # Create installer instance with silent logger
            temp_installer = PrinterInstaller(
                printer_ip=ip,
                branch="main",
                password="creality_2024",
            )
            temp_installer.logger.setLevel(logging.CRITICAL)
            temp_installer.logger.handlers.clear()
            
            print(f"[BACKUP_CACHE_DEBUG_THREAD] Starting query for IP: {ip}")
            sizes = temp_installer.query_backup_sizes()
            temp_installer.executor.close()
            
            # Check if we got any actual data (not just empty defaults)
            moonraker_info = sizes.get("moonraker", {})
            timelapses_info = sizes.get("timelapses", {})
            has_data = moonraker_info.get("exists", False) or timelapses_info.get("exists", False)
            
            print(f"[BACKUP_CACHE_DEBUG_THREAD] Query completed - moonraker exists: {moonraker_info.get('exists')}, timelapses exists: {timelapses_info.get('exists')}")
            
            # Only cache if query succeeded AND we got valid data or successfully connected
            # (empty data is valid if connection succeeded - means no files exist)
            print(f"[BACKUP_CACHE_DEBUG_THREAD] Caching results")
            self.backup_sizes_cache = {
                "ip": ip,
                "timestamp": time.time(),
                "sizes": sizes,
            }
            
            # Update UI with results (query was successful, so hide timelapse if it doesn't exist)
            self._update_ui_with_sizes(sizes, query_successful=True)
            
            # Hide loading indicators
            if self.backup_dialog_moonraker_loading.current:
                self.backup_dialog_moonraker_loading.current.visible = False
            if self.backup_dialog_timelapses_loading.current:
                self.backup_dialog_timelapses_loading.current.visible = False
            if self.backup_dialog_gcodes_loading.current:
                self.backup_dialog_gcodes_loading.current.visible = False
                
        except SSHConnectionError as exc:
            # SSH connection failed - don't cache anything, preserve existing cache if any
            print(f"[BACKUP_CACHE_DEBUG_THREAD] SSH Connection FAILED: {exc}")
            # Just hide loading indicators
            if self.backup_dialog_moonraker_loading.current:
                self.backup_dialog_moonraker_loading.current.visible = False
            if self.backup_dialog_timelapses_loading.current:
                self.backup_dialog_timelapses_loading.current.visible = False
            if self.backup_dialog_gcodes_loading.current:
                self.backup_dialog_gcodes_loading.current.visible = False
            # Don't update cache - preserve existing cache for this IP if it exists
        except Exception as exc:
            # Other query failures - don't cache anything, preserve existing cache if any
            print(f"[BACKUP_CACHE_DEBUG_THREAD] Query FAILED: {type(exc).__name__}: {exc}")
            # Just hide loading indicators
            if self.backup_dialog_moonraker_loading.current:
                self.backup_dialog_moonraker_loading.current.visible = False
            if self.backup_dialog_timelapses_loading.current:
                self.backup_dialog_timelapses_loading.current.visible = False
            if self.backup_dialog_gcodes_loading.current:
                self.backup_dialog_gcodes_loading.current.visible = False
            # Don't update cache - preserve existing cache for this IP if it exists
        
        self.page.update()

    def show_backup_dialog(self, e=None):
        """Show backup location selection dialog."""
        self.create_backup_dialog()
        if self.backup_dialog:
            # Clear previous path and size info
            if self.backup_dialog_path_field.current:
                self.backup_dialog_path_field.current.value = ""
            if self.backup_dialog_moonraker_size_text.current:
                self.backup_dialog_moonraker_size_text.current.value = ""
            if self.backup_dialog_timelapses_size_text.current:
                self.backup_dialog_timelapses_size_text.current.value = ""
            if self.backup_dialog_gcodes_size_text.current:
                self.backup_dialog_gcodes_size_text.current.value = ""
            
            # Ensure loading indicators are hidden initially
            if self.backup_dialog_moonraker_loading.current:
                self.backup_dialog_moonraker_loading.current.visible = False
            if self.backup_dialog_timelapses_loading.current:
                self.backup_dialog_timelapses_loading.current.visible = False
            if self.backup_dialog_gcodes_loading.current:
                self.backup_dialog_gcodes_loading.current.visible = False
            
            # Ensure all rows are visible initially (checkboxes will be disabled if components don't exist)
            if self.backup_dialog_timelapses_row.current:
                self.backup_dialog_timelapses_row.current.visible = True
            if self.backup_dialog_gcodes_row.current:
                self.backup_dialog_gcodes_row.current.visible = True
            
            # Reset checkboxes to enabled state initially (will be disabled if query confirms no components exist)
            if self.backup_dialog_moonraker_checkbox.current:
                self.backup_dialog_moonraker_checkbox.current.disabled = False
                self.backup_dialog_moonraker_checkbox.current.value = True
            if self.backup_dialog_timelapses_checkbox.current:
                self.backup_dialog_timelapses_checkbox.current.disabled = False
                self.backup_dialog_timelapses_checkbox.current.value = True
            if self.backup_dialog_gcodes_checkbox.current:
                self.backup_dialog_gcodes_checkbox.current.disabled = False
                self.backup_dialog_gcodes_checkbox.current.value = True
            
            self.backup_dialog.open = True
            
            # Check cache synchronously before starting background thread
            ip = self.ip_field.current.value.strip() if self.ip_field.current else ""
            cache_valid = False
            if ip and self.backup_sizes_cache is not None:
                cache = self.backup_sizes_cache
                cache_ip = cache.get("ip")
                cache_timestamp = cache.get("timestamp", 0)
                current_time = time.time()
                time_diff = current_time - cache_timestamp
                
                # Debug logging
                print(f"[BACKUP_CACHE_DEBUG] Checking cache for IP: {ip}")
                print(f"[BACKUP_CACHE_DEBUG] Cache IP: {cache_ip}, Cache timestamp: {cache_timestamp}")
                print(f"[BACKUP_CACHE_DEBUG] Current time: {current_time}, Time diff: {time_diff}s, Expiry: {self.CACHE_EXPIRY_SECONDS}s")
                
                # Check if IP matches and cache hasn't expired
                if cache_ip == ip and time_diff < self.CACHE_EXPIRY_SECONDS:
                    cache_valid = True
                    print(f"[BACKUP_CACHE_DEBUG] Cache VALID - using cached data")
                    # Use cached data immediately - cache is validated, so we can trust it
                    sizes = cache.get("sizes", {})
                    self._update_ui_with_sizes(sizes, query_successful=True)
                else:
                    print(f"[BACKUP_CACHE_DEBUG] Cache INVALID - IP match: {cache_ip == ip}, Expired: {time_diff >= self.CACHE_EXPIRY_SECONDS}")
            else:
                print(f"[BACKUP_CACHE_DEBUG] No cache or no IP - cache exists: {self.backup_sizes_cache is not None}, IP: {ip}")
            
            self.page.update()
            
            # Only start background query if cache is not valid
            if not cache_valid and ip:
                print(f"[BACKUP_CACHE_DEBUG] Starting background query thread")
                threading.Thread(target=self.query_backup_sizes_async, daemon=True).start()
            else:
                print(f"[BACKUP_CACHE_DEBUG] Skipping background query - cache_valid: {cache_valid}, ip: {ip}")

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

    def on_backup_component_changed(self, e):
        """Update Start Backup button state based on checkbox selection."""
        # Only check enabled checkboxes
        moonraker_checked = (
            self.backup_dialog_moonraker_checkbox.current.value
            if (self.backup_dialog_moonraker_checkbox.current and 
                not self.backup_dialog_moonraker_checkbox.current.disabled)
            else False
        )
        timelapses_checked = (
            self.backup_dialog_timelapses_checkbox.current.value
            if (self.backup_dialog_timelapses_checkbox.current and 
                not self.backup_dialog_timelapses_checkbox.current.disabled)
            else False
        )
        gcodes_checked = (
            self.backup_dialog_gcodes_checkbox.current.value
            if (self.backup_dialog_gcodes_checkbox.current and 
                not self.backup_dialog_gcodes_checkbox.current.disabled)
            else False
        )
        
        # Disable button if nothing is selected
        if self.backup_dialog_start_btn.current:
            self.backup_dialog_start_btn.current.disabled = not (moonraker_checked or timelapses_checked or gcodes_checked)
            self.page.update()

    def handle_backup_dialog_confirm(self, e):
        """Execute backup with selected components."""
        # Validate that backup path is not empty
        backup_path = (
            self.backup_dialog_path_field.current.value.strip()
            if self.backup_dialog_path_field.current
            else ""
        )
        if not backup_path:
            self.show_snackbar("Please select a backup directory", ft.Colors.ORANGE)
            return
        
        # Get checkbox states for enabled checkboxes only
        moonraker_checked = (
            self.backup_dialog_moonraker_checkbox.current.value
            if (self.backup_dialog_moonraker_checkbox.current and 
                not self.backup_dialog_moonraker_checkbox.current.disabled)
            else False
        )
        timelapses_checked = (
            self.backup_dialog_timelapses_checkbox.current.value
            if (self.backup_dialog_timelapses_checkbox.current and 
                not self.backup_dialog_timelapses_checkbox.current.disabled)
            else False
        )
        gcodes_checked = (
            self.backup_dialog_gcodes_checkbox.current.value
            if (self.backup_dialog_gcodes_checkbox.current and 
                not self.backup_dialog_gcodes_checkbox.current.disabled)
            else False
        )
        
        # Validate that at least one component is selected
        if not moonraker_checked and not timelapses_checked and not gcodes_checked:
            self.show_snackbar("Please select at least one component to backup", ft.Colors.ORANGE)
            return
        
        # Dismiss dialog after validation passes
        self.dismiss_backup_dialog()
        
        # Call run_command with action "backup" and pass component flags
        self.run_command(
            "backup",
            backup_dir=backup_path,
            backup_moonraker=moonraker_checked,
            backup_timelapses=timelapses_checked,
            backup_gcodes=gcodes_checked,
        )

    def create_restore_dialog(self):
        """Create restore dialog with validation and component selection."""
        if self.restore_dialog:
            return

        dialog_content = ft.Column(
            [
                ft.Text("Select a backup directory to restore from."),
                ft.Row(
                    [
                        ft.TextField(
                            ref=self.restore_dialog_path_field,
                            label="Backup Directory",
                            hint_text="/path/to/backup",
                            value="",
                            dense=True,
                            expand=1,
                            on_change=self.on_restore_path_changed,
                        ),
                        ft.FilledButton(
                            text="Browse",
                            on_click=self.on_restore_dialog_browse_click,
                            height=36,
                        ),
                    ],
                    spacing=8,
                ),
                ft.Container(
                    ref=self.restore_dialog_checkboxes_container,
                    visible=False,
                    content=ft.Column(
                        [
                            ft.Divider(),
                            ft.Text("Components to restore:", size=13, weight=ft.FontWeight.W_500),
                            ft.Row(
                                [
                                    ft.Checkbox(
                                        ref=self.restore_dialog_moonraker_checkbox,
                                        label="Moonraker Stats",
                                        value=False,
                                        visible=False,
                                        on_change=self.on_restore_component_changed,
                                    ),
                                    ft.Text(
                                        ref=self.restore_dialog_moonraker_size_text,
                                        size=11,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(
                                [
                                    ft.Checkbox(
                                        ref=self.restore_dialog_timelapses_checkbox,
                                        label="Timelapses",
                                        value=False,
                                        visible=False,
                                        on_change=self.on_restore_component_changed,
                                    ),
                                    ft.Text(
                                        ref=self.restore_dialog_timelapses_size_text,
                                        size=11,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(
                                [
                                    ft.Checkbox(
                                        ref=self.restore_dialog_gcodes_checkbox,
                                        label="GCodes",
                                        value=False,
                                        visible=False,
                                        on_change=self.on_restore_component_changed,
                                    ),
                                    ft.Text(
                                        ref=self.restore_dialog_gcodes_size_text,
                                        size=11,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        spacing=8,
                    ),
                ),
            ],
            spacing=12,
            tight=True,
        )

        self.restore_dialog = ft.AlertDialog(
            modal=False,
            content_padding=ft.padding.symmetric(horizontal=16, vertical=12),
            actions_alignment=ft.MainAxisAlignment.END,
            title=ft.Text("Restore"),
            content=dialog_content,
            actions=[
                ft.TextButton(
                    "Cancel",
                    on_click=self.dismiss_restore_dialog,
                ),
                ft.TextButton(
                    ref=self.restore_dialog_start_btn,
                    text="Start Restore",
                    on_click=self.handle_restore_dialog_confirm,
                    disabled=True,  # Initially disabled until path is validated
                ),
            ],
            on_dismiss=self.dismiss_restore_dialog,
        )
        self.page.overlay.append(self.restore_dialog)

    def show_restore_dialog(self, e=None):
        """Show restore dialog."""
        self.create_restore_dialog()
        if self.restore_dialog:
            # Reset state
            if self.restore_dialog_path_field.current:
                self.restore_dialog_path_field.current.value = ""
            self.restore_dialog_checkboxes_container.current.visible = False
            self.restore_dialog_start_btn.current.disabled = True
            self.restore_dialog.open = True
            self.page.update()

    def dismiss_restore_dialog(self, e=None):
        """Close restore dialog."""
        if self.restore_dialog:
            self.restore_dialog.open = False
            self.page.update()

    def on_restore_dialog_browse_click(self, e):
        """Open directory picker for restore dialog."""
        self.restore_picker.get_directory_path(
            dialog_title="Select Backup Directory",
            initial_directory=self.current_dir
        )

    def _query_local_backup_sizes(self, backup_dir: Path) -> dict[str, dict[str, int | bool]]:
        """Query backup sizes from local directory. Returns dict with moonraker, timelapses, and gcodes info."""
        result = {
            "moonraker": {"count": 0, "size_kb": 0, "exists": False},
            "timelapses": {"count": 0, "size_kb": 0, "exists": False},
            "gcodes": {"count": 0, "size_kb": 0, "exists": False},
        }
        
        # Check Moonraker database files
        moonraker_files = []
        for filename in ["data.mdb", "moonraker-sql.db"]:
            file_path = backup_dir / filename
            if file_path.exists():
                moonraker_files.append(file_path)
        
        if moonraker_files:
            result["moonraker"]["exists"] = True
            result["moonraker"]["count"] = len(moonraker_files)
            total_size = sum(f.stat().st_size for f in moonraker_files)
            result["moonraker"]["size_kb"] = total_size // 1024  # Convert bytes to KB
        
        # Check timelapse files
        timelapse_dir = backup_dir / "timelapse"
        if timelapse_dir.exists() and timelapse_dir.is_dir():
            timelapse_files = [f for f in timelapse_dir.iterdir() if f.is_file()]
            if timelapse_files:
                result["timelapses"]["exists"] = True
                result["timelapses"]["count"] = len(timelapse_files)
                total_size = sum(f.stat().st_size for f in timelapse_files)
                result["timelapses"]["size_kb"] = total_size // 1024  # Convert bytes to KB
        
        # Check gcode files
        gcodes_dir = backup_dir / "gcodes"
        if gcodes_dir.exists() and gcodes_dir.is_dir():
            gcode_files = [f for f in gcodes_dir.iterdir() if f.is_file()]
            if gcode_files:
                result["gcodes"]["exists"] = True
                result["gcodes"]["count"] = len(gcode_files)
                total_size = sum(f.stat().st_size for f in gcode_files)
                result["gcodes"]["size_kb"] = total_size // 1024  # Convert bytes to KB
        
        return result

    def _update_restore_ui_with_sizes(self, sizes: dict[str, dict[str, int | bool]], has_moonraker: bool, has_timelapses: bool, has_gcodes: bool = False):
        """Update restore dialog UI with size information."""
        moonraker_info = sizes.get("moonraker", {})
        timelapses_info = sizes.get("timelapses", {})
        gcodes_info = sizes.get("gcodes", {})
        
        # Update Moonraker display
        if moonraker_info.get("exists"):
            count = moonraker_info.get("count", 0)
            size_kb = moonraker_info.get("size_kb", 0)
            size_str = self.format_size(size_kb)
            if self.restore_dialog_moonraker_size_text.current:
                self.restore_dialog_moonraker_size_text.current.value = f"{count} file{'s' if count != 1 else ''}, {size_str}"
            # Enable checkbox if moonraker exists
            if self.restore_dialog_moonraker_checkbox.current:
                self.restore_dialog_moonraker_checkbox.current.disabled = False
                self.restore_dialog_moonraker_checkbox.current.label_style = None
        else:
            if self.restore_dialog_moonraker_size_text.current:
                self.restore_dialog_moonraker_size_text.current.value = "None detected"
            # Disable checkbox if moonraker doesn't exist (but only if other components exist, otherwise already disabled)
            if self.restore_dialog_moonraker_checkbox.current and (has_timelapses or has_gcodes):
                self.restore_dialog_moonraker_checkbox.current.disabled = True
                self.restore_dialog_moonraker_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
        
        # Update Timelapses display
        if timelapses_info.get("exists"):
            count = timelapses_info.get("count", 0)
            size_kb = timelapses_info.get("size_kb", 0)
            size_str = self.format_size(size_kb)
            if self.restore_dialog_timelapses_size_text.current:
                self.restore_dialog_timelapses_size_text.current.value = f"{count} file{'s' if count != 1 else ''}, {size_str}"
            # Enable checkbox if timelapses exist
            if self.restore_dialog_timelapses_checkbox.current:
                self.restore_dialog_timelapses_checkbox.current.disabled = False
                self.restore_dialog_timelapses_checkbox.current.label_style = None
        else:
            if self.restore_dialog_timelapses_size_text.current:
                self.restore_dialog_timelapses_size_text.current.value = "None detected"
            # Disable checkbox if timelapses don't exist (but only if other components exist, otherwise already disabled)
            if self.restore_dialog_timelapses_checkbox.current and (has_moonraker or has_gcodes):
                self.restore_dialog_timelapses_checkbox.current.disabled = True
                self.restore_dialog_timelapses_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
        
        # Update GCodes display
        if gcodes_info.get("exists"):
            count = gcodes_info.get("count", 0)
            size_kb = gcodes_info.get("size_kb", 0)
            size_str = self.format_size(size_kb)
            if self.restore_dialog_gcodes_size_text.current:
                self.restore_dialog_gcodes_size_text.current.value = f"{count} file{'s' if count != 1 else ''}, {size_str}"
            # Enable checkbox if gcodes exist
            if self.restore_dialog_gcodes_checkbox.current:
                self.restore_dialog_gcodes_checkbox.current.disabled = False
                self.restore_dialog_gcodes_checkbox.current.label_style = None
        else:
            if self.restore_dialog_gcodes_size_text.current:
                self.restore_dialog_gcodes_size_text.current.value = "None detected"
            # Disable checkbox if gcodes don't exist (but only if other components exist, otherwise already disabled)
            if self.restore_dialog_gcodes_checkbox.current and (has_moonraker or has_timelapses):
                self.restore_dialog_gcodes_checkbox.current.disabled = True
                self.restore_dialog_gcodes_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )

    def on_restore_path_changed(self, e):
        """Validate backup directory and show available components."""
        backup_path = self.restore_dialog_path_field.current.value.strip()
        
        # Clear size displays initially
        if self.restore_dialog_moonraker_size_text.current:
            self.restore_dialog_moonraker_size_text.current.value = ""
        if self.restore_dialog_timelapses_size_text.current:
            self.restore_dialog_timelapses_size_text.current.value = ""
        if self.restore_dialog_gcodes_size_text.current:
            self.restore_dialog_gcodes_size_text.current.value = ""
        
        # Check if path is empty → hide checkboxes, disable button
        if not backup_path:
            self.restore_dialog_checkboxes_container.current.visible = False
            self.restore_dialog_start_btn.current.disabled = True
            self.page.update()
            return
        
        # Check if path exists and is a directory → show error if not
        backup_dir = Path(backup_path)
        
        if not backup_dir.exists():
            self.show_snackbar("Selected path does not exist", ft.Colors.ORANGE)
            self.restore_dialog_checkboxes_container.current.visible = False
            self.restore_dialog_start_btn.current.disabled = True
            self.page.update()
            return
        
        if not backup_dir.is_dir():
            self.show_snackbar("Selected path is not a directory", ft.Colors.ORANGE)
            self.restore_dialog_checkboxes_container.current.visible = False
            self.restore_dialog_start_btn.current.disabled = True
            self.page.update()
            return
        
        # Check for Moonraker stats files (data.mdb or moonraker-sql.db)
        has_moonraker = (
            (backup_dir / "data.mdb").exists() or 
            (backup_dir / "moonraker-sql.db").exists()
        )
        
        # Check for timelapse subdirectory
        has_timelapses = (backup_dir / "timelapse").exists() and (backup_dir / "timelapse").is_dir()
        
        # Check for gcodes subdirectory
        has_gcodes = (backup_dir / "gcodes").exists() and (backup_dir / "gcodes").is_dir()
        
        # Show error if no valid components found
        if not has_moonraker and not has_timelapses and not has_gcodes:
            self.show_snackbar("No valid backup components found in directory", ft.Colors.ORANGE)
            self.restore_dialog_checkboxes_container.current.visible = False
            self.restore_dialog_start_btn.current.disabled = True
            self.page.update()
            return
        
        # Show checkboxes container if at least one component exists
        self.restore_dialog_checkboxes_container.current.visible = True
        
        # Always show all checkboxes (they'll be greyed out if component doesn't exist)
        self.restore_dialog_moonraker_checkbox.current.visible = True
        self.restore_dialog_timelapses_checkbox.current.visible = True
        self.restore_dialog_gcodes_checkbox.current.visible = True
        
        # Query local backup sizes and update UI
        sizes = self._query_local_backup_sizes(backup_dir)
        self._update_restore_ui_with_sizes(sizes, has_moonraker, has_timelapses, has_gcodes)
        
        # Count how many components exist
        component_count = sum([has_moonraker, has_timelapses, has_gcodes])
        
        # Configure Moonraker checkbox initial state
        if has_moonraker:
            self.restore_dialog_moonraker_checkbox.current.value = True
            # Disable if it's the only option (user can't uncheck it)
            self.restore_dialog_moonraker_checkbox.current.disabled = component_count == 1
            if component_count == 1:
                self.restore_dialog_moonraker_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
        else:
            self.restore_dialog_moonraker_checkbox.current.value = False
            self.restore_dialog_moonraker_checkbox.current.disabled = True
            self.restore_dialog_moonraker_checkbox.current.label_style = ft.TextStyle(
                color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
            )
        
        # Configure Timelapses checkbox initial state
        if has_timelapses:
            self.restore_dialog_timelapses_checkbox.current.value = True
            # Disable if it's the only option (user can't uncheck it)
            self.restore_dialog_timelapses_checkbox.current.disabled = component_count == 1
            if component_count == 1:
                self.restore_dialog_timelapses_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
        else:
            self.restore_dialog_timelapses_checkbox.current.value = False
            self.restore_dialog_timelapses_checkbox.current.disabled = True
            self.restore_dialog_timelapses_checkbox.current.label_style = ft.TextStyle(
                color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
            )
        
        # Configure GCodes checkbox initial state
        if has_gcodes:
            self.restore_dialog_gcodes_checkbox.current.value = True
            # Disable if it's the only option (user can't uncheck it)
            self.restore_dialog_gcodes_checkbox.current.disabled = component_count == 1
            if component_count == 1:
                self.restore_dialog_gcodes_checkbox.current.label_style = ft.TextStyle(
                    color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
                )
        else:
            self.restore_dialog_gcodes_checkbox.current.value = False
            self.restore_dialog_gcodes_checkbox.current.disabled = True
            self.restore_dialog_gcodes_checkbox.current.label_style = ft.TextStyle(
                color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
            )
        
        # Enable Start Restore button since at least one component exists
        self.restore_dialog_start_btn.current.disabled = False
        
        self.page.update()

    def on_restore_component_changed(self, e):
        """Update Start Restore button state based on checkbox selection."""
        # Check if at least one enabled checkbox is selected
        moonraker_checked = (
            self.restore_dialog_moonraker_checkbox.current.value 
            if (self.restore_dialog_moonraker_checkbox.current.visible and 
                not self.restore_dialog_moonraker_checkbox.current.disabled)
            else False
        )
        timelapses_checked = (
            self.restore_dialog_timelapses_checkbox.current.value 
            if (self.restore_dialog_timelapses_checkbox.current.visible and 
                not self.restore_dialog_timelapses_checkbox.current.disabled)
            else False
        )
        gcodes_checked = (
            self.restore_dialog_gcodes_checkbox.current.value 
            if (self.restore_dialog_gcodes_checkbox.current.visible and 
                not self.restore_dialog_gcodes_checkbox.current.disabled)
            else False
        )
        
        # Disable Start Restore button when all enabled checkboxes are unchecked
        # Enable Start Restore button when at least one checkbox is checked
        if self.restore_dialog_start_btn.current:
            self.restore_dialog_start_btn.current.disabled = not (moonraker_checked or timelapses_checked or gcodes_checked)
            self.page.update()

    def handle_restore_dialog_confirm(self, e):
        """Execute restore with selected components."""
        # Validate that backup path is not empty
        backup_path = self.restore_dialog_path_field.current.value.strip()
        
        if not backup_path:
            self.show_snackbar("Please select a backup directory", ft.Colors.ORANGE)
            return
        
        # Get checkbox states for enabled checkboxes
        moonraker_checked = (
            self.restore_dialog_moonraker_checkbox.current.value 
            if (self.restore_dialog_moonraker_checkbox.current.visible and 
                not self.restore_dialog_moonraker_checkbox.current.disabled)
            else False
        )
        timelapses_checked = (
            self.restore_dialog_timelapses_checkbox.current.value 
            if (self.restore_dialog_timelapses_checkbox.current.visible and 
                not self.restore_dialog_timelapses_checkbox.current.disabled)
            else False
        )
        gcodes_checked = (
            self.restore_dialog_gcodes_checkbox.current.value 
            if (self.restore_dialog_gcodes_checkbox.current.visible and 
                not self.restore_dialog_gcodes_checkbox.current.disabled)
            else False
        )
        
        # Validate that at least one component is selected
        if not moonraker_checked and not timelapses_checked and not gcodes_checked:
            self.show_snackbar("Please select at least one component to restore", ft.Colors.ORANGE)
            return
        
        # Dismiss dialog after validation passes
        self.dismiss_restore_dialog()
        
        # Call run_command with action "restore" and pass component flags
        self.run_command(
            "restore",
            backup_dir=backup_path,
            restore_moonraker=moonraker_checked,
            restore_timelapses=timelapses_checked,
            restore_gcodes=gcodes_checked,
        )

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
            backup_moonraker = kwargs.get("backup_moonraker", False)
            backup_timelapses = kwargs.get("backup_timelapses", False)
            backup_gcodes = kwargs.get("backup_gcodes", False)
            
            if not backup_dir:
                self.show_snackbar("Please select a backup directory", ft.Colors.ORANGE)
                return None
            
            # Validate that at least one component is selected
            if not backup_moonraker and not backup_timelapses and not backup_gcodes:
                self.show_snackbar("Please select at least one component", ft.Colors.ORANGE)
                return None
            
            # Use --backup-only with component flags
            args.extend(["--backup-only", backup_dir])
            if backup_moonraker:
                args.append("--backup-moonraker")
            if backup_timelapses:
                args.append("--backup-timelapses")
            if backup_gcodes:
                args.append("--backup-gcodes")
        elif action == "restore":
            backup_dir = kwargs.get("backup_dir", "")
            restore_moonraker = kwargs.get("restore_moonraker", False)
            restore_timelapses = kwargs.get("restore_timelapses", False)
            restore_gcodes = kwargs.get("restore_gcodes", False)
            
            if not backup_dir:
                self.show_snackbar("Please select a backup directory", ft.Colors.ORANGE)
                return None
            
            # Validate that at least one component is selected
            if not restore_moonraker and not restore_timelapses and not restore_gcodes:
                self.show_snackbar("Please select at least one component", ft.Colors.ORANGE)
                return None
            
            # Use --restore-only with component flags
            args.extend(["--restore-only", backup_dir])
            if restore_moonraker:
                args.append("--restore-moonraker")
            if restore_timelapses:
                args.append("--restore-timelapses")
            if restore_gcodes:
                args.append("--restore-gcodes")
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
            if self.preserve_timelapses_checkbox.current.value:
                args.append("--preserve-timelapses")
            if self.preserve_gcodes_checkbox.current.value:
                args.append("--preserve-gcodes")
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

#!/usr/bin/env python3
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import customtkinter as ctk

# Configuration Variables
ESTIMATOR_PATH = "/Applications/klipper_estimator_osx"
MOONRAKER_URL = "http://192.168.1.4:7125"
MOONRAKER_TIMEOUT = 2  # Timeout in seconds for connectivity check

# Default Heat Soak Time in minutes
DEFAULT_HEAT_SOAK_TIME = "5.0"

ENABLE_HEAT_SOAK_CONFIG = True
ENABLE_REMOVE_DUPLICATE_TOOL = True
ENABLE_REMOVE_SPIRAL_MOVE = True
ENABLE_KLIPPER_ESTIMATOR = True
ENABLE_TOOLCHANGE_M104_WAIT = True

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)

WINDOW_BG = "#f5f5f7"
CARD_BG = "#ffffff"
TEXT_PRIMARY = "#1f2933"
TEXT_SECONDARY = "#4b5563"
ACCENT_COLOR = "#2563eb"
DANGER_COLOR = "#d13d3d"
NEUTRAL_BUTTON_BG = "#e5e7eb"

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


@dataclass
class ConnectivityStatus:
    checked: bool = True
    connected: bool = True
    message: str = ""


moonraker_connectivity = ConnectivityStatus()


class ProcessingCancelled(Exception):
    """Raised when the user intentionally aborts post-processing."""

    def __init__(self, message: str = "", *, blank_file: bool = False, show_auto_close: bool = False):
        super().__init__(message)
        self.blank_file = blank_file
        self.show_auto_close = show_auto_close


@dataclass
class ProcessingReport:
    messages: List[str] = None
    warnings: List[str] = None

    def __post_init__(self):
        if self.messages is None:
            self.messages = []
        if self.warnings is None:
            self.warnings = []

    def add_message(self, message: str) -> None:
        if message:
            self.messages.append(message)

    def add_warning(self, warning: str) -> None:
        if warning:
            self.warnings.append(warning)


@dataclass
class GCodeDocument:
    path: Path
    lines: List[str]

    @classmethod
    def load(cls, path: Path) -> "GCodeDocument":
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Error: G-code file '{path}' not found.") from exc
        return cls(path=path, lines=text.splitlines(True))

    def ensure_trailing_newline(self) -> None:
        if self.lines and not self.lines[-1].endswith("\n"):
            self.lines[-1] += "\n"

    def write(self) -> None:
        self.ensure_trailing_newline()
        self.path.write_text("".join(self.lines), encoding="utf-8")

    def replace_text(self, transform: Callable[[str], str]) -> None:
        text = "".join(self.lines)
        new_text = transform(text)
        self.lines = new_text.splitlines(True)

    def append_status(self, messages: Sequence[str]) -> None:
        self.ensure_trailing_newline()
        for message in messages:
            self.lines.append(message + "\n")


def safe_write(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logging.error("Failed to write to %s: %s", path, exc)

def wipe_gcode_file(path: Path, reason: str) -> None:
    try:
        safe_write(path, f"; {reason}\n")
    except Exception as exc:
        logging.error("Failed to wipe G-code file %s: %s", path, exc)


def _center_window(root: ctk.CTk, width: int, height: int) -> None:
    root.update_idletasks()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width - width) // 2
    y = (screen_height - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")


def create_window(
    title: str,
    width: int,
    height: int,
    *,
    bg: str = WINDOW_BG,
    resizable: Tuple[bool, bool] = (False, False),
) -> ctk.CTk:
    root = ctk.CTk()
    root.title(title)
    root.configure(bg=bg)
    root.resizable(*resizable)
    root.attributes("-topmost", True)
    _center_window(root, width, height)
    root.configure(highlightthickness=0)
    return root


def _apply_card_style(frame: ctk.CTkFrame) -> None:
    frame.configure(fg_color=CARD_BG, corner_radius=16)


def show_auto_close_popup():
    """Show a popup for 2 seconds indicating that a blank STL is being uploaded to cancel the slice."""
    try:
        root = create_window("Canceling Slice", width=360, height=160)
        container = ctk.CTkFrame(root, fg_color=WINDOW_BG)
        container.pack(fill="both", expand=True, padx=16, pady=16)

        card = ctk.CTkFrame(container, fg_color=CARD_BG)
        _apply_card_style(card)
        card.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(
            card,
            text="Uploading blank STL to cancel this slice...",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=DANGER_COLOR,
            justify="center",
        ).pack(pady=(6, 4))


        root.after(1500, root.destroy)
        root.mainloop()
    except Exception as exc:
        logging.debug("Auto close popup failed: %s", exc)


def show_error_popup(error_message):
    """Show a popup that displays the error message until the user closes it."""
    try:
        root = create_window(
            "Processing Error",
            width=520,
            height=320,
            resizable=(True, True),
        )

        container = ctk.CTkFrame(root, fg_color=WINDOW_BG)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        card = ctk.CTkFrame(container, fg_color=CARD_BG)
        _apply_card_style(card)
        card.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(
            card,
            text="Uploading a blank STL to cancel this job due to an error:",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=DANGER_COLOR,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        text_box = ctk.CTkTextbox(
            card,
            height=140,
            corner_radius=12,
            fg_color="#f8fafc",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
            wrap="word",
        )
        text_box.insert("0.0", error_message)
        text_box.configure(state="disabled")
        text_box.pack(fill="both", expand=True)



        ctk.CTkButton(
            card,
            text="Close",
            command=root.destroy,
            fg_color=ACCENT_COLOR,
            hover_color="#1d4ed8",
            text_color="#ffffff",
            height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="e")

        root.mainloop()
    except Exception as exc:
        logging.error("Error popup failed: %s", exc)

def handle_error_and_exit(gcode_file: Path, error_message: str) -> None:
    logging.error("Post-processing failed: %s", error_message)
    show_error_popup(error_message)
    wipe_gcode_file(gcode_file, "G-code file cleared due to processing error")
    sys.exit(0)

def _parse_host_port(url: str) -> Tuple[str, int]:
    if url.startswith("http://"):
        host_port = url[7:]
    elif url.startswith("https://"):
        host_port = url[8:]
    else:
        host_port = url

    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        if "/" in port_str:
            port_str = port_str.split("/", 1)[0]
        try:
            port = int(port_str)
        except ValueError as exc:
            raise ValueError(f"Invalid port in Moonraker URL: {url}") from exc
    else:
        host = host_port
        port = 80

    return host, port


def check_moonraker_connectivity() -> Tuple[bool, str]:
    """Check if Moonraker server is accessible with a short timeout."""
    try:
        host, port = _parse_host_port(MOONRAKER_URL)
    except ValueError as exc:
        return False, str(exc)

    start_time = time.time()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(MOONRAKER_TIMEOUT)
        try:
            sock.connect((host, port))
        except socket.timeout:
            return False, f"Connection to Moonraker at {host}:{port} timed out after {MOONRAKER_TIMEOUT}s"
        except socket.error as exc:
            return False, f"Failed to connect to Moonraker at {host}:{port}: {exc}"

    elapsed = time.time() - start_time
    return True, f"Connected to Moonraker at {host}:{port} in {elapsed:.2f}s"

def background_connectivity_check() -> None:
    global moonraker_connectivity
    is_connected, message = check_moonraker_connectivity()
    moonraker_connectivity.checked = True
    moonraker_connectivity.connected = is_connected
    moonraker_connectivity.message = message

def start_connectivity_check() -> Optional[threading.Thread]:
    """Start a background thread to check Moonraker connectivity."""
    if not ENABLE_KLIPPER_ESTIMATOR:
        return None

    thread = threading.Thread(target=background_connectivity_check, name="moonraker-check")
    thread.daemon = True
    thread.start()
    return thread

def wait_for_connectivity_check(thread: Optional[threading.Thread]) -> None:
    """Wait for connectivity check to complete if it hasn't already."""
    if not thread:
        return

    max_wait = MOONRAKER_TIMEOUT
    deadline = time.time() + max_wait

    while thread.is_alive() and time.time() < deadline:
        time.sleep(0.1)

    if thread.is_alive():
        moonraker_connectivity.checked = True
        moonraker_connectivity.connected = False
        moonraker_connectivity.message = f"Connectivity check timed out after {max_wait}s"


def remove_duplicate_tool(lines: List[str]) -> Tuple[List[str], str]:
    """Remove duplicate tool selection before the first layer."""
    # Find the first tool selection (T0-T5)
    initial_tool = None
    initial_tool_index = -1
    
    for i, line in enumerate(lines):
        tool_match = re.match(r'^T([0-5])\s*', line.strip())
        if tool_match:
            initial_tool = tool_match.group(0).split(';')[0].strip()
            initial_tool_index = i
            break
    
    # Process the G-code
    status_message = ""
    if initial_tool is None:
        status_message = "; Tool selection removal: No initial tool selection (T0-T5) found in G-code"
    else:
        # Special handling for T4 - remove BOTH occurrences
        if initial_tool == "T4":
            # Comment out the first T4
            original_line = lines[initial_tool_index].rstrip()
            lines[initial_tool_index] = f"; REMOVED T4 (FIRST OCCURRENCE): {original_line}\n"
            
            # Look for the second occurrence of T4
            found_second = False
            second_tool_index = -1
            
            for i in range(initial_tool_index + 1, len(lines)):
                line = lines[i].strip()
                
                # Stop searching if we hit layer change
                if ";LAYER_CHANGE" in line:
                    break
                    
                # Check if this line matches T4
                if line.startswith("T4"):
                    found_second = True
                    second_tool_index = i
                    break
            
            # Comment out the second occurrence if found
            if found_second:
                original_line = lines[second_tool_index].rstrip()
                lines[second_tool_index] = f"; REMOVED T4 (SECOND OCCURRENCE): {original_line}\n"
                status_message = f"; Tool selection removal: T4 detected - removed BOTH occurrences at lines {initial_tool_index+1} and {second_tool_index+1}"
            else:
                status_message = f"; Tool selection removal: T4 detected - removed first occurrence at line {initial_tool_index+1}, no second occurrence found before first layer"
        
        else:
            # Original logic for all other tools (T0-T3, T5)
            # Look for the second occurrence of the same tool selection
            found_second = False
            second_tool_index = -1
            
            for i in range(initial_tool_index + 1, len(lines)):
                line = lines[i].strip()
                
                # Stop searching if we hit layer change
                if ";LAYER_CHANGE" in line:
                    break
                    
                # Check if this line matches our initial tool
                if line.startswith(initial_tool):
                    found_second = True
                    second_tool_index = i
                    break
            
            # Comment out the second occurrence if found
            if found_second:
                original_line = lines[second_tool_index].rstrip()
                lines[second_tool_index] = f"; REMOVED DUPLICATE TOOL: {original_line}\n"
                status_message = f"; Tool selection removal: Successfully commented out duplicate {initial_tool} command at line {second_tool_index+1} (before first layer)"
            else:
                status_message = f"; Tool selection removal: No duplicate {initial_tool} found before first layer. Initial {initial_tool} found at line {initial_tool_index+1}"

    # Append status comment (will be added at the end of the whole process)
    return lines, status_message

def remove_filament_swap_spiral(lines: List[str]) -> Tuple[List[str], str]:
    # Define the three key lines that make up the erroneous filament swap spiral movement
    first   = "G2 Z0.4 I0.86 J0.86 P1 F10000 ; spiral lift a little from second lift\n"
    second  = "G1 X0 Y245 F30000\n"
    third   = "G1 Z0 F600\n"

    removed = False
    reason = ""
    
    # Track positions of key lines
    first_pos = -1
    second_pos = -1
    third_pos = -1
    
    # Scan for the block, allowing other lines in between
    n = len(lines)
    for i in range(n):
        # If we hit the filament start marker first, give up
        if lines[i].strip() == "; filament start gcode":
            reason = "hit '; filament start gcode' before finding complete sequence"
            break
            
        # Look for our key lines in order
        if first_pos == -1 and lines[i] == first:
            first_pos = i
        elif first_pos != -1 and second_pos == -1 and lines[i] == second:
            second_pos = i
        elif first_pos != -1 and second_pos != -1 and third_pos == -1 and lines[i] == third:
            third_pos = i
            # Found all three in the correct order - comment out these three lines
            # Comment from last to first to avoid index shifting
            lines[third_pos] = f"; REMOVED FILAMENT SWAP SPIRAL (PART 3/3): {lines[third_pos].rstrip()}\n"
            lines[second_pos] = f"; REMOVED FILAMENT SWAP SPIRAL (PART 2/3): {lines[second_pos].rstrip()}\n"
            lines[first_pos] = f"; REMOVED FILAMENT SWAP SPIRAL (PART 1/3): {lines[first_pos].rstrip()}\n"
            removed = True
            break

    if not removed and not reason:
        reason = "filament swap spiral sequence not found in expected format"

    # Prepare status message
    if removed:
        status_message = f"; Filament swap spiral removal: Successfully commented out erroneous spiral lift-move-lower commands at original lines {first_pos+1}, {second_pos+1}, and {third_pos+1}"
    else:
        status_message = f"; Filament swap spiral removal: {reason}. Searched for 'G2 Z0.4...' → 'G1 X0 Y245...' → 'G1 Z0 F600...'"
        
    return lines, status_message

def replace_m104_after_toolchange(lines):
    """Within each '; CP TOOLCHANGE START'..'; CP TOOLCHANGE END' block, find the last T<number>,
    then the last M104 with an S value after that T, and insert a Klipper TEMPERATURE_WAIT line
    immediately after it, bounded by ±2C around the original S. Only insert if S >= 200.
    Append inline comment on the inserted line.

    Returns updated lines, a summary status message, and an optional low-temp warning message.
    """
    toolchange_count = 0
    inserted_count = 0
    low_temp_count = 0

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("; CP TOOLCHANGE START"):
            toolchange_count += 1

            # Find the end of this toolchange block
            end_idx = i + 1
            while end_idx < n and not lines[end_idx].lstrip().startswith("; CP TOOLCHANGE END"):
                end_idx += 1

            # Find the last T<number> within the block
            last_t_index = -1
            for idx in range(i + 1, min(end_idx, n)):
                stripped = lines[idx].lstrip()
                if stripped.startswith(';'):
                    continue
                if re.match(r'^T(\d+)\b', stripped, flags=re.IGNORECASE):
                    last_t_index = idx

            if last_t_index != -1:
                # Find the last M104 with S after the last T within the block
                last_m104_index = -1
                last_m104_s_str = None
                for idx in range(last_t_index + 1, min(end_idx, n)):
                    stripped = lines[idx].lstrip()
                    if stripped.startswith(';'):
                        continue
                    if re.match(r'^M104\b', stripped, flags=re.IGNORECASE):
                        s_match = re.search(r'\bS\s*(-?\d+(?:\.\d+)?)\b', stripped, flags=re.IGNORECASE)
                        if s_match:
                            last_m104_index = idx
                            last_m104_s_str = s_match.group(1)

                if last_m104_index != -1 and last_m104_s_str is not None:
                    try:
                        s_value = float(last_m104_s_str)
                    except Exception:
                        s_value = None

                    if s_value is not None and s_value >= 200:
                        min_str = f"{s_value - 2:g}"
                        max_str = f"{s_value + 2:g}"
                        leading_ws_match = re.match(r'^(\s*)', lines[last_m104_index])
                        leading_ws = leading_ws_match.group(1) if leading_ws_match else ''
                        inserted_line = (
                            f"{leading_ws}TEMPERATURE_WAIT SENSOR=extruder MINIMUM={min_str} MAXIMUM={max_str} "
                            f";M104 S{last_m104_s_str} wait inserted.\n"
                        )
                        lines.insert(last_m104_index + 1, inserted_line)
                        inserted_count += 1
                    else:
                        low_temp_count += 1

            # Advance to after the end marker (or EOF if not found)
            i = end_idx + 1 if end_idx < n else n
        else:
            i += 1

    summary_message = f"; {toolchange_count} toolchanges detected and {inserted_count} wait commands inserted after M104 commands"
    low_temp_warning = None
    if low_temp_count > 0:
        low_temp_warning = (
            f"; Warning: {low_temp_count} M104 commands below 200 found in toolchange blocks; no wait added"
        )

    return lines, summary_message, low_temp_warning

def show_heat_soak_gui() -> Optional[float]:
    root = create_window("Heat Soak Time", width=380, height=260)

    soak_time_var = ctk.StringVar(value=DEFAULT_HEAT_SOAK_TIME)
    apply_enabled = [True]
    soak_time_result: List[Optional[float]] = [None]
    user_closed_window = [True]

    container = ctk.CTkFrame(root, fg_color=WINDOW_BG)
    container.pack(fill="both", expand=True, padx=18, pady=18)

    card = ctk.CTkFrame(container, fg_color=CARD_BG)
    _apply_card_style(card)
    card.pack(fill="both", expand=True, padx=6, pady=6)

    ctk.CTkLabel(
        card,
        text="Set heat soak duration (minutes).",
        font=ctk.CTkFont(size=15, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(0, 20))

    input_frame = ctk.CTkFrame(card, fg_color="transparent")
    input_frame.pack(anchor="center", pady=(12, 20))

    def adjust_soak(delta: float) -> None:
        current = validate_soak_value(soak_time_var.get())
        new_value = 0.0 if current is None else max(0.0, current + delta)
        soak_time_var.set(f"{new_value:g}")

    decrement_button = ctk.CTkButton(
        input_frame,
        text="-",
        width=32,
        height=32,
        font=ctk.CTkFont(size=18, weight="bold"),
        fg_color=NEUTRAL_BUTTON_BG,
        text_color=TEXT_PRIMARY,
        hover_color="#d1d5db",
        command=lambda: adjust_soak(-1.0),
    )
    decrement_button.pack(side="left", padx=(0, 6), pady=4)

    value_container = ctk.CTkFrame(input_frame, fg_color="#eef2ff", corner_radius=8)
    value_container.pack(side="left", padx=6, pady=4)

    time_entry = ctk.CTkEntry(
        value_container,
        textvariable=soak_time_var,
        width=90,
        justify="center",
        font=ctk.CTkFont(size=16, weight="bold"),
        fg_color="transparent",
        border_width=0,
    )
    time_entry.pack(padx=6, pady=6)

    increment_button = ctk.CTkButton(
        input_frame,
        text="+",
        width=32,
        height=32,
        font=ctk.CTkFont(size=18, weight="bold"),
        fg_color=ACCENT_COLOR,
        text_color="#ffffff",
        hover_color="#1d4ed8",
        command=lambda: adjust_soak(1.0),
    )
    increment_button.pack(side="left", padx=(6, 0), pady=4)

    status_label = ctk.CTkLabel(
        card,
        text="",
        font=ctk.CTkFont(size=12),
        text_color=DANGER_COLOR,
        justify="center",
    )
    status_label.pack(fill="x", pady=(8, 0))

    def finish_processing(soak_time: float) -> None:
        soak_time_result[0] = soak_time
        user_closed_window[0] = False
        root.destroy()

    def validate_soak_value(raw_value: str) -> Optional[float]:
        try:
            value = float(raw_value)
        except ValueError:
            return None
        return value if value >= 0 else None

    def update_validation_feedback(*_args) -> None:
        value = soak_time_var.get().strip()
        normalized = None if value == "" else validate_soak_value(value)
        if normalized is None:
            status_label.configure(text="Enter a non-negative number (minutes).", text_color=DANGER_COLOR)
            apply_button.configure(state="disabled")
            apply_enabled[0] = False
        else:
            status_label.configure(text="", text_color=DANGER_COLOR)
            if not apply_enabled[0]:
                apply_button.configure(state="normal")
            apply_enabled[0] = True

    def process_file(soak_value: Optional[float] = None) -> None:
        if soak_value is not None:
            soak_time = soak_value
        else:
            normalized = validate_soak_value(soak_time_var.get())
            if normalized is None:
                update_validation_feedback()
                return
            soak_time = normalized

        status_label.configure(text="Processing G-code...", text_color=ACCENT_COLOR)
        no_soak_button.configure(state="disabled")
        apply_button.configure(state="disabled")
        root.after(120, lambda: finish_processing(soak_time))

    button_frame = ctk.CTkFrame(card, fg_color="transparent")
    button_frame.pack(fill="x", pady=(10, 0))

    no_soak_button = ctk.CTkButton(
        button_frame,
        text="No Heat Soak",
        command=lambda: process_file(0.0),
        fg_color=NEUTRAL_BUTTON_BG,
        text_color=TEXT_PRIMARY,
        hover_color="#d1d5db",
        font=ctk.CTkFont(size=13, weight="bold"),
        width=140,
        height=40,
    )
    no_soak_button.pack(side="left", padx=(0, 12))

    apply_button = ctk.CTkButton(
        button_frame,
        text="Apply",
        command=process_file,
        fg_color=ACCENT_COLOR,
        hover_color="#1d4ed8",
        text_color="#ffffff",
        font=ctk.CTkFont(size=13, weight="bold"),
        width=140,
        height=40,
    )
    apply_button.pack(side="right")

    soak_time_var.trace_add("write", lambda *_args: update_validation_feedback())
    update_validation_feedback()

    time_entry.bind("<Return>", lambda _event: process_file())
    time_entry.focus_set()
    root.mainloop()

    if user_closed_window[0]:
        raise ProcessingCancelled(
            "Heat soak window closed without selection",
            blank_file=True,
            show_auto_close=True,
        )

    return soak_time_result[0]

def apply_heat_soak(document: GCodeDocument, soak_time: float) -> str:
    pattern = r"(START_PRINT\s+[^;\n]*?)(\s*;|\s*\n)"

    def add_soak_time(match: re.Match) -> str:
        start_print_cmd = match.group(1)
        line_end = match.group(2)

        if "SOAK_TIME=" in start_print_cmd:
            modified_cmd = re.sub(r"SOAK_TIME=\S+", f"SOAK_TIME={soak_time}", start_print_cmd)
        else:
            modified_cmd = f"{start_print_cmd} SOAK_TIME={soak_time}"

        return modified_cmd + line_end

    try:
        document.replace_text(lambda text: re.sub(pattern, add_soak_time, text))
        return f"; Heat soak: Set to {soak_time} minutes in START_PRINT command"
    except Exception as exc:
        raise Exception(f"Heat soak configuration error: {exc}")

def show_moonraker_connectivity_popup() -> bool:
    try:
        root = create_window("Moonraker Connectivity Issue", width=420, height=240)
        user_choice = [False]
        selection_made = [False]

        container = ctk.CTkFrame(root, fg_color=WINDOW_BG)
        container.pack(fill="both", expand=True, padx=18, pady=18)

        card = ctk.CTkFrame(container, fg_color=CARD_BG)
        _apply_card_style(card)
        card.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(
            card,
            text="Moonraker connection failed.",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=DANGER_COLOR,
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkLabel(
            card,
            text="Klipper Estimator requires Moonraker. Continue without running it?",
            font=ctk.CTkFont(size=13),
            text_color=TEXT_PRIMARY,
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))

        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(fill="x")

        def continue_without() -> None:
            user_choice[0] = True
            selection_made[0] = True
            root.destroy()

        def cancel_processing() -> None:
            user_choice[0] = False
            selection_made[0] = True
            root.destroy()

        ctk.CTkButton(
            button_frame,
            text="Continue",
            command=continue_without,
            fg_color=NEUTRAL_BUTTON_BG,
            hover_color="#d1d5db",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=13, weight="bold"),
            width=140,
            height=40,
        ).pack(side="left")

        ctk.CTkButton(
            button_frame,
            text="Cancel",
            command=cancel_processing,
            fg_color=DANGER_COLOR,
            hover_color="#b91c1c",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=140,
            height=40,
        ).pack(side="right")

        root.mainloop()
        return user_choice[0] if selection_made[0] else False
    except Exception as exc:
        logging.error("Moonraker connectivity popup failed: %s", exc)
        return False

def run_klipper_estimator(gcode_file: Path, report: ProcessingReport) -> None:
    if not moonraker_connectivity.connected:
        if not show_moonraker_connectivity_popup():
            raise ProcessingCancelled(
                "User cancelled due to Moonraker connectivity failure",
                blank_file=True,
                show_auto_close=True,
            )
        report.add_warning("; Klipper Estimator: Skipped due to Moonraker connectivity issue")
        return

    cmd = [ESTIMATOR_PATH, "--config_moonraker_url", MOONRAKER_URL, "post-process", str(gcode_file)]
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except subprocess.SubprocessError as exc:
        raise Exception(f"Klipper Estimator invocation error: {exc}")

    if process.returncode != 0:
        stderr = process.stderr.strip()
        raise Exception(
            f"Klipper Estimator failed with exit code {process.returncode}."
            f" Error output: {stderr or 'No stderr provided.'}"
        )

    report.add_message("; Klipper Estimator: Successfully run")

def main():
    if len(sys.argv) != 2:
        print("Usage: python combined_script.py <gcode_file>")
        sys.exit(1)

    gcode_path = Path(sys.argv[1])
    report = ProcessingReport()
    soak_time: Optional[float] = None

    connectivity_thread = start_connectivity_check()

    try:
        if ENABLE_HEAT_SOAK_CONFIG:
            soak_time = show_heat_soak_gui()
        else:
            report.add_message("; Heat soak configuration: Disabled")

        document = GCodeDocument.load(gcode_path)

        if ENABLE_HEAT_SOAK_CONFIG and soak_time is not None:
            status_message = apply_heat_soak(document, soak_time)
            report.add_message(status_message)

        if ENABLE_REMOVE_DUPLICATE_TOOL:
            document.lines, status_message = remove_duplicate_tool(document.lines)
            report.add_message(status_message)
        else:
            report.add_message("; Tool selection removal: Disabled")

        if ENABLE_REMOVE_SPIRAL_MOVE:
            document.lines, status_message = remove_filament_swap_spiral(document.lines)
            report.add_message(status_message)
        else:
            report.add_message("; Filament swap spiral removal: Disabled")

        if ENABLE_TOOLCHANGE_M104_WAIT:
            document.lines, summary_message, low_temp_warning = replace_m104_after_toolchange(document.lines)
            report.add_message(summary_message)
            report.add_warning(low_temp_warning)
        else:
            report.add_message("; Toolchange M104 replacement: Disabled")

        document.write()

        wait_for_connectivity_check(connectivity_thread)

        if ENABLE_KLIPPER_ESTIMATOR:
            run_klipper_estimator(gcode_path, report)
        else:
            report.add_message("; Klipper Estimator: Disabled")

        document = GCodeDocument.load(gcode_path)
        status_lines: List[str] = [msg for msg in report.messages if msg]
        status_lines.extend(warning for warning in report.warnings if warning)
        document.append_status(status_lines)
        document.write()

    except ProcessingCancelled as exc:
        logging.info("Processing cancelled: %s", exc or exc.__class__.__name__)
        popup_shown = False
        if exc.show_auto_close:
            show_auto_close_popup()
            popup_shown = True
        if exc.blank_file:
            wipe_gcode_file(gcode_path, "G-code file cleared due to user cancellation")
            if not popup_shown:
                show_auto_close_popup()
        sys.exit(0)
    except Exception as exc:
        handle_error_and_exit(gcode_path, str(exc))


if __name__ == "__main__":
    main()
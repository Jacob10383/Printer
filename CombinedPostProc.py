#!/usr/bin/env python3
import sys
import re
import tkinter as tk
from tkinter import messagebox
import subprocess
import socket
import time
import threading

# Configuration Variables
ESTIMATOR_PATH = "/Applications/klipper_estimator_osx"
MOONRAKER_URL = "http://192.168.1.4:7125"
MOONRAKER_TIMEOUT = 3  # Timeout in seconds for connectivity check

# Default Heat Soak Time in minutes
DEFAULT_HEAT_SOAK_TIME = "5.0"

ENABLE_HEAT_SOAK_CONFIG = True
ENABLE_REMOVE_DUPLICATE_TOOL = True
ENABLE_REMOVE_SPIRAL_MOVE = True
ENABLE_KLIPPER_ESTIMATOR = True
ENABLE_BRIM_DETECTION = False
ENABLE_TOOLCHANGE_M104_WAIT = True

# Global variable to store moonraker connectivity result
moonraker_connectivity = {"checked": True, "connected": True, "message": ""}

def show_auto_close_popup():
    """Show a popup for 2 seconds indicating that a blank STL is being uploaded to cancel the slice."""
    try:
        root = tk.Tk()
        root.title("Canceling Slice")
        root.geometry("350x100")
        root.resizable(False, False)
        root.configure(bg="#f0f0f0")
        
        root.attributes('-topmost', True)
        
        # Center the window
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width - 350) // 2
        y = (screen_height - 100) // 2
        root.geometry(f"350x100+{x}+{y}")
        
        # Create the message
        label = tk.Label(root, text="uploading blank stl to cancel slice", 
                        font=("Arial", 12), bg="#f0f0f0", fg="#cc0000")
        label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        # Close after 2 seconds
        root.after(2000, root.destroy)
        
        root.mainloop()
    except:
        # If popup fails, just continue
        pass

def show_error_popup(error_message):
    """Show a popup that displays the error message and stays open until user closes it."""
    try:
        root = tk.Tk()
        root.title("Processing Error")
        root.geometry("500x200")
        root.resizable(True, True)
        root.configure(bg="#f0f0f0")
        
        root.attributes('-topmost', True)
        
        # Center the window
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width - 500) // 2
        y = (screen_height - 200) // 2
        root.geometry(f"500x200+{x}+{y}")
        
        # Create main frame
        main_frame = tk.Frame(root, bg="#f0f0f0", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title label
        title_label = tk.Label(main_frame, text="Uploading blank stl to cancel slice due to error:", 
                              font=("Arial", 12, "bold"), bg="#f0f0f0", fg="#cc0000")
        title_label.pack(pady=(0, 10))
        
        # Error message in a text widget with scrollbar
        text_frame = tk.Frame(main_frame, bg="#f0f0f0")
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("Arial", 10), 
                             bg="#ffffff", fg="#000000", relief=tk.SUNKEN, bd=2)
        scrollbar = tk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Insert error message
        text_widget.insert(tk.END, error_message)
        text_widget.config(state=tk.DISABLED)  # Make it read-only
        
        # Close button
        close_button = tk.Button(main_frame, text="Close", width=10, height=2, 
                                command=root.destroy, bg="#e6e6e6", relief=tk.RAISED, font=("Arial", 10))
        close_button.pack()
        
        root.mainloop()
    except:
        # If popup fails, just continue
        pass

def handle_error_and_exit(gcode_file, error_message):
    """Handle any error by showing popup with error, wiping gcode file, and exiting cleanly."""
    # Show the error popup (stays open until user closes)
    show_error_popup(error_message)
    
    # Wipe the gcode file
    try:
        with open(gcode_file, 'w', encoding='utf-8') as f:
            f.write("; G-code file cleared due to processing error\n")
    except:
        pass  # If we can't write, just ignore it
    
    # Exit cleanly
    sys.exit(0)

def check_moonraker_connectivity():
    """Check if Moonraker server is accessible with a short timeout."""
    # Extract the host and port from the Moonraker URL
    if MOONRAKER_URL.startswith("http://"):
        host_port = MOONRAKER_URL[7:]  # remove http://
    elif MOONRAKER_URL.startswith("https://"):
        host_port = MOONRAKER_URL[8:]  # remove https://
    else:
        host_port = MOONRAKER_URL
    
    # Split host and port
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        # Remove any path after the port
        if "/" in port_str:
            port_str = port_str.split("/", 1)[0]
        try:
            port = int(port_str)
        except ValueError:
            return False, f"Invalid port in Moonraker URL: {MOONRAKER_URL}"
    else:
        # Default to port 80 if none specified
        host = host_port
        port = 80
    
    # Create a socket and set a timeout
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(MOONRAKER_TIMEOUT)
    
    try:
        # Try to connect to the host:port
        start_time = time.time()
        s.connect((host, port))
        end_time = time.time()
        s.close()
        return True, f"Connected to Moonraker at {host}:{port} in {end_time - start_time:.2f}s"
    except socket.timeout:
        return False, f"Connection to Moonraker at {host}:{port} timed out after {MOONRAKER_TIMEOUT}s"
    except socket.error as e:
        return False, f"Failed to connect to Moonraker at {host}:{port}: {str(e)}"
    finally:
        s.close()

def background_connectivity_check():
    """Function to run in a background thread to check Moonraker connectivity."""
    global moonraker_connectivity
    is_connected, message = check_moonraker_connectivity()
    
    # Store the results
    moonraker_connectivity["checked"] = True
    moonraker_connectivity["connected"] = is_connected
    moonraker_connectivity["message"] = message

def start_connectivity_check():
    """Start a background thread to check Moonraker connectivity."""
    if ENABLE_KLIPPER_ESTIMATOR:
        thread = threading.Thread(target=background_connectivity_check)
        thread.daemon = True  # Thread will exit when main program exits
        thread.start()
        return thread
    return None

def wait_for_connectivity_check(thread):
    """Wait for connectivity check to complete if it hasn't already."""
    global moonraker_connectivity
    
    if not ENABLE_KLIPPER_ESTIMATOR:
        return
    
    # If the connectivity check hasn't completed yet, wait for it
    if thread and thread.is_alive():
        max_wait = MOONRAKER_TIMEOUT
        start_time = time.time()
        
        while thread.is_alive() and time.time() - start_time < max_wait:
            time.sleep(0.1)  # Small sleep to prevent CPU spinning
        
        # If thread is still running after timeout, it's likely stuck
        if thread.is_alive():
            moonraker_connectivity["checked"] = True
            moonraker_connectivity["connected"] = False
            moonraker_connectivity["message"] = f"Connectivity check timed out after {max_wait}s"

def detect_and_inject_brim_width(gcode_file, lines):
    """Detect brim width by analyzing actual brim G-code movements."""
    brim_width = None
    detection_method = None
    
    # First, try to get object bounds from EXCLUDE_OBJECT
    object_bounds = None
    for line in lines:
        if line.startswith("EXCLUDE_OBJECT_DEFINE"):
            polygon_match = re.search(r'POLYGON=\[\[(.*?)\]\]', line)
            if polygon_match:
                try:
                    coords_str = polygon_match.group(1)
                    coords = []
                    for pair in coords_str.split('],['):
                        x, y = pair.strip('[]').split(',')
                        coords.append((float(x), float(y)))
                    
                    if coords:
                        x_coords = [coord[0] for coord in coords]
                        y_coords = [coord[1] for coord in coords]
                        obj_x_min, obj_x_max = min(x_coords), max(x_coords)
                        obj_y_min, obj_y_max = min(y_coords), max(y_coords)
                        object_bounds = (obj_x_min, obj_x_max, obj_y_min, obj_y_max)
                        break
                except:
                    continue
    
    # Look for brim_object_gap in comments - search the LAST 200 lines instead
    brim_gap = 0.0
    search_start = max(0, len(lines) - 2000)
    for line in lines[search_start:]:  # Search last 2000 lines for settings
        stripped = line.strip()
        if "brim_object_gap" in stripped:
            try:
                # More flexible regex to handle various whitespace patterns
                gap_match = re.search(r'brim_object_gap\s*=\s*([0-9]*\.?[0-9]+)', stripped)
                if gap_match:
                    brim_gap = float(gap_match.group(1))
                    break
            except:
                continue
    
    # Find brim section
    brim_start = -1
    brim_end = -1
    line_width = None
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        if stripped == ";TYPE:Brim":
            brim_start = i
        elif brim_start != -1 and brim_end == -1 and stripped.startswith(";WIDTH:"):
            try:
                line_width = float(stripped.split(":")[1])
            except:
                pass
        elif brim_start != -1 and stripped.startswith(";TYPE:") and stripped != ";TYPE:Brim":
            brim_end = i
            break
    
    if brim_start != -1 and line_width is not None and object_bounds is not None:
        # Extract coordinates from brim section, but exclude interior brims
        brim_coords = []
        end_idx = brim_end if brim_end != -1 else len(lines)
        obj_x_min, obj_x_max, obj_y_min, obj_y_max = object_bounds
        obj_center_x = (obj_x_min + obj_x_max) / 2
        obj_center_y = (obj_y_min + obj_y_max) / 2
        
        for i in range(brim_start, end_idx):
            line = lines[i].strip()
            if line.startswith("G1 ") and "X" in line and "Y" in line:
                try:
                    x_match = re.search(r'X([0-9.-]+)', line)
                    y_match = re.search(r'Y([0-9.-]+)', line)
                    if x_match and y_match:
                        x = float(x_match.group(1))
                        y = float(y_match.group(1))
                        
                        # Only include coordinates that are OUTSIDE the object bounds
                        # This excludes interior brims (holes, etc.)
                        if (x < obj_x_min - 0.1 or x > obj_x_max + 0.1 or 
                            y < obj_y_min - 0.1 or y > obj_y_max + 0.1):
                            brim_coords.append((x, y))
                except:
                    continue
        
        if len(brim_coords) > 10:
            # Get brim bounds (only exterior brim centerlines)
            x_coords = [coord[0] for coord in brim_coords]
            y_coords = [coord[1] for coord in brim_coords]
            brim_x_min, brim_x_max = min(x_coords), max(x_coords)
            brim_y_min, brim_y_max = min(y_coords), max(y_coords)
            
            # Account for line width - coordinates are centerlines, so add half line width to get true edges
            half_line_width = line_width / 2
            true_brim_x_min = brim_x_min - half_line_width
            true_brim_x_max = brim_x_max + half_line_width
            true_brim_y_min = brim_y_min - half_line_width
            true_brim_y_max = brim_y_max + half_line_width
            
            # Calculate total distance from object edge to true brim edge
            left_total = obj_x_min - true_brim_x_min
            right_total = true_brim_x_max - obj_x_max
            bottom_total = obj_y_min - true_brim_y_min
            top_total = true_brim_y_max - obj_y_max

            # Take the maximum (should be roughly equal on all sides)
            total_distance = max(left_total, right_total, bottom_total, top_total)

            # For adaptive purging, we want the TOTAL clearance needed (gap + brim width)
            brim_width = total_distance

            detection_method = f"EXCLUDE_OBJECT bounds vs true brim edges (total clearance needed:{total_distance:.2f}mm, gap:{brim_gap}mm + brim_width ≈ {total_distance-brim_gap:.2f}mm, line_width:{line_width}mm)"
            
            # Check if brim width is > 15mm and show warning popup
            if brim_width > 15:
                def show_brim_warning():
                    import tkinter as tk
                    from tkinter import messagebox
                    
                    root = tk.Tk()
                    root.title("Brim Width Warning")
                    root.geometry("400x140")
                    root.resizable(False, False)
                    root.configure(bg="#f0f0f0")
                    
                    root.attributes('-topmost', True)
                    
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    x = (screen_width - 400) // 2
                    y = (screen_height - 140) // 2
                    root.geometry(f"400x140+{x}+{y}")
                    
                    user_accepted = [False]  # Flag to track if user accepted
                    user_closed_window = [True]  # Flag to track if window was closed without selection
                    
                    main_frame = tk.Frame(root, bg="#f0f0f0", padx=20, pady=20)
                    main_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
                    
                    warning_label = tk.Label(main_frame, text=f"Warning: Detected brim margin is {brim_width:.2f}mm", 
                                           font=("Arial", 12, "bold"), bg="#f0f0f0", fg="#cc0000")
                    warning_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
                    
                    def accept_and_close():
                        user_accepted[0] = True
                        user_closed_window[0] = False
                        root.destroy()
                    
                    def abort_and_close():
                        user_accepted[0] = False
                        user_closed_window[0] = False
                        root.destroy()
                    
                    button_frame = tk.Frame(main_frame, bg="#f0f0f0")
                    button_frame.grid(row=1, column=0, columnspan=2)
                    
                    accept_button = tk.Button(button_frame, text="Accept", width=14, height=2, 
                                            command=accept_and_close, bg="#e6e6e6", relief=tk.RAISED, font=("Arial", 10))
                    accept_button.grid(row=0, column=0, padx=10)
                    
                    abort_button = tk.Button(button_frame, text="Abort", width=14, height=2, 
                                           command=abort_and_close, bg="#e6e6e6", relief=tk.RAISED, font=("Arial", 10))
                    abort_button.grid(row=0, column=1, padx=10)
                    
                    root.mainloop()
                    
                    # After mainloop exits, check the result
                    if user_closed_window[0] or not user_accepted[0]:
                        raise Exception("Brim width warning aborted: Large brim margin detected and user chose to abort processing")
                    
                    return True
                
                # Show the warning popup
                show_brim_warning()
    
    # Inject the variable if detected
    if brim_width is not None and brim_width > 0:
        # Find injection point - after comments but before actual G-code
        injection_point = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == '' or stripped.startswith(';'):
                continue
            injection_point = i
            break
        
        # Round to reasonable precision and ensure minimum value
        brim_width = max(round(brim_width, 2), 0.1)
        
        variable_command = f"SET_GCODE_VARIABLE MACRO=_KAMP_Settings VARIABLE=detected_brim_width VALUE={brim_width}\n"
        lines.insert(injection_point, variable_command)
        
        status_message = f"; Brim detection: Found brim width {brim_width}mm using {detection_method}, injected SET_GCODE_VARIABLE command at line {injection_point + 1}"
    else:
        status_message = "; Brim detection: No brim section found, no EXCLUDE_OBJECT bounds found, or unable to calculate brim width"
    
    return lines, status_message

def remove_duplicate_tool(gcode_file, lines):
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
def remove_filament_swap_spiral(gcode_file, lines):
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

def show_heat_soak_gui(gcode_file):
    root = tk.Tk()
    root.title("Heat Soak Time")
    root.geometry("340x160")  # Slightly taller to accommodate status message
    root.resizable(False, False)
    root.configure(bg="#f0f0f0")
    
    root.attributes('-topmost', True)
    
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width - 340) // 2
    y = (screen_height - 160) // 2
    root.geometry(f"340x160+{x}+{y}")
    
    soak_time_var = tk.StringVar(value=DEFAULT_HEAT_SOAK_TIME)
    soak_time_result = [None]  # Use a list to store the result
    user_closed_window = [True]  # Flag to track if window was closed without selection
    
    main_frame = tk.Frame(root, bg="#f0f0f0", padx=20, pady=20)
    main_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
    
    time_label = tk.Label(main_frame, text="Minutes:", font=("Arial", 10), bg="#f0f0f0")
    time_label.grid(row=0, column=0, sticky=tk.E, padx=(0, 10))
    
    time_entry = tk.Entry(main_frame, textvariable=soak_time_var, width=10, font=("Arial", 10))
    time_entry.grid(row=0, column=1, sticky=tk.W)
    
    # Status label for processing indication
    status_label = tk.Label(main_frame, text="", font=("Arial", 10), bg="#f0f0f0", fg="#0066cc")
    status_label.grid(row=2, column=0, columnspan=2, pady=(10, 0))
    
    def finish_processing(soak_time):
        # Store the result
        soak_time_result[0] = soak_time
        user_closed_window[0] = False
        # Close the window
        root.destroy()
    
    def process_file(soak_value=None):
        try:
            if soak_value is not None:
                soak_time = soak_value
            else:
                try:
                    soak_time = float(soak_time_var.get())
                    if soak_time < 0:
                        messagebox.showerror("Invalid Input", "Heat soak time cannot be negative")
                        return
                except ValueError:
                    messagebox.showerror("Invalid Input", "Please enter a valid number")
                    return
            
            # Update status and disable buttons
            status_label.config(text="Processing G-code, please wait...")
            no_soak_button.config(state=tk.DISABLED)
            apply_button.config(state=tk.DISABLED)
            
            # Schedule finish_processing to run after a short delay
            # This allows the GUI to update before we destroy the window
            root.after(100, lambda: finish_processing(soak_time))
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to process G-code file:\n{str(e)}")
    
    button_frame = tk.Frame(main_frame, bg="#f0f0f0", pady=20)
    button_frame.grid(row=1, column=0, columnspan=2)
    
    no_soak_button = tk.Button(button_frame, text="No Heat Soak", width=14, height=2, command=lambda: process_file(0), bg="#e6e6e6", relief=tk.RAISED, font=("Arial", 10))
    no_soak_button.grid(row=0, column=0, padx=10)
    
    apply_button = tk.Button(button_frame, text="Apply", width=14, height=2, command=process_file, bg="#e6e6e6", relief=tk.RAISED, font=("Arial", 10))
    apply_button.grid(row=0, column=1, padx=10)
    
    def on_enter(event):
        process_file()
    
    time_entry.bind("<Return>", on_enter)
    
    time_entry.focus_set()
    root.mainloop()
    
    # After mainloop exits, check if the user closed without selection
    if user_closed_window[0]:
        # Show the auto-close popup for heat soak window closure
        show_auto_close_popup()
        
        # Wipe the gcode file instead of throwing an error
        try:
            with open(gcode_file, 'w', encoding='utf-8') as f:
                f.write("; G-code file cleared due to heat soak window being closed without selection\n")
        except:
            pass  # If we can't write, just ignore it
        return "ABORT"  # Special return value to signal abortion
    
    return soak_time_result[0]

def apply_heat_soak(gcode_file, soak_time):
    try:
        with open(gcode_file, 'r', encoding='utf-8') as f:
            gcode = f.read()
        
        pattern = r'(START_PRINT\s+[^;\n]*?)(\s*;|\s*\n)'
        
        def add_soak_time(match):
            start_print_cmd = match.group(1)
            line_end = match.group(2)
            
            if 'SOAK_TIME=' in start_print_cmd:
                modified_cmd = re.sub(r'SOAK_TIME=\S+', f'SOAK_TIME={soak_time}', start_print_cmd)
            else:
                modified_cmd = f"{start_print_cmd} SOAK_TIME={soak_time}"
            
            return modified_cmd + line_end
        
        modified_gcode = re.sub(pattern, add_soak_time, gcode)
        
        with open(gcode_file, 'w', encoding='utf-8') as f:
            f.write(modified_gcode)
            
        return f"; Heat soak: Set to {soak_time} minutes in START_PRINT command"
        
    except Exception as e:
        # Raise the exception to abort processing
        raise Exception(f"Heat soak configuration error: {str(e)}")

def run_klipper_estimator(gcode_file):
    try:
        global moonraker_connectivity
        
        # Check the result of the connectivity test
        if not moonraker_connectivity["connected"]:
            raise Exception(f"Cannot connect to Moonraker server: {moonraker_connectivity['message']}")
        
        # If we got here, we can connect to Moonraker, so run the estimator
        cmd = [ESTIMATOR_PATH, "--config_moonraker_url", MOONRAKER_URL, "post-process", gcode_file]
        process = subprocess.run(cmd, capture_output=True, text=True)
        
        # Check if the command was successful - raise exception on failure
        if process.returncode != 0:
            error_msg = f"Klipper Estimator failed with error code {process.returncode}. Error: {process.stderr.strip()}"
            raise Exception(error_msg)
        
        return None
            
    except subprocess.SubprocessError as e:
        # Raise the exception to abort processing
        raise Exception(f"Klipper Estimator error: {str(e)}")
    except FileNotFoundError:
        # Specific error for executable not found
        raise Exception(f"Klipper Estimator executable not found at: {ESTIMATOR_PATH}")
    except Exception as e:
        # Catch and re-raise any other exceptions
        raise Exception(f"Klipper Estimator error: {str(e)}")

def main():
    if len(sys.argv) != 2:
        print("Usage: python combined_script.py <gcode_file>")
        sys.exit(1)

    gcode_file = sys.argv[1]
    
    try:
        status_messages = []

        # Start connectivity check in background thread
        connectivity_thread = start_connectivity_check()
        
        # Step 1: Show GUI for heat soak time (if enabled)
        if ENABLE_HEAT_SOAK_CONFIG:
            soak_time = show_heat_soak_gui(gcode_file)
            if soak_time == "ABORT":
                # File has already been wiped, popup already shown, exit cleanly
                sys.exit(0)
            if soak_time is not None:
                status_message = apply_heat_soak(gcode_file, soak_time)
                status_messages.append(status_message)
        else:
            status_messages.append("; Heat soak configuration: Disabled")

        # Read the (possibly modified) file for the next steps
        try:
            with open(gcode_file, "r", encoding='utf-8') as f:
                lines = f.readlines()
        except FileNotFoundError:
            # This will cause Orca to abort
            raise Exception(f"Error: G-code file '{gcode_file}' not found.")

        # Step 2: Detect and inject brim width (if enabled)
        if ENABLE_BRIM_DETECTION:
            lines, status_message = detect_and_inject_brim_width(gcode_file, lines)
            status_messages.append(status_message)
        else:
            status_messages.append("; Brim detection: Disabled")

        # Step 3: Remove duplicate tool selection (if enabled)
        if ENABLE_REMOVE_DUPLICATE_TOOL:
            lines, status_message = remove_duplicate_tool(gcode_file, lines)
            status_messages.append(status_message)
        else:
            status_messages.append("; Tool selection removal: Disabled")

        # Step 4: Remove filament swap spiral (if enabled)
        if ENABLE_REMOVE_SPIRAL_MOVE:
            lines, status_message = remove_filament_swap_spiral(gcode_file, lines)
            status_messages.append(status_message)
        else:
            status_messages.append("; Filament swap spiral removal: Disabled")

        # Step 5: Replace M104 with TEMPERATURE_WAIT inside toolchange blocks (if enabled)
        if ENABLE_TOOLCHANGE_M104_WAIT:
            lines, summary_message, low_temp_warning = replace_m104_after_toolchange(lines)
            status_messages.append(summary_message)
            if low_temp_warning:
                status_messages.append(low_temp_warning)
        else:
            status_messages.append("; Toolchange M104 replacement: Disabled")

        # Write intermediate changes to file
        try:
            with open(gcode_file, "w", encoding='utf-8') as f:
                f.writelines(lines)
        except Exception as e:
            # This will cause Orca to abort
            raise Exception(f"Error writing to G-code file: {str(e)}")
        
        # Wait for connectivity check to complete if it hasn't already
        wait_for_connectivity_check(connectivity_thread)
        
        # Step 6: Run Klipper Estimator on the modified file (if enabled)
        if ENABLE_KLIPPER_ESTIMATOR:
            # This will raise an exception on failure
            run_klipper_estimator(gcode_file)
            status_messages.append("; Klipper Estimator: Successfully run")
        else:
            status_messages.append("; Klipper Estimator: Disabled")
        
        # Re-read the file (as it may have been modified by the estimator)
        try:
            with open(gcode_file, "r", encoding='utf-8') as f:
                lines = f.readlines()
        except FileNotFoundError:
            # This will cause Orca to abort
            raise Exception(f"Error: G-code file '{gcode_file}' not found after running estimator.")

        # Ensure last line ends with newline, then append all status comments
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        
        for message in status_messages:
            lines.append(message + "\n")

        # Write back out with status comments
        try:
            with open(gcode_file, "w", encoding='utf-8') as f:
                f.writelines(lines)
        except Exception as e:
            # This will cause Orca to abort
            raise Exception(f"Error writing final G-code: {str(e)}")
    
    except Exception as e:
        # Handle any error by showing popup with error message, wiping file, and exiting cleanly
        handle_error_and_exit(gcode_file, str(e))

if __name__ == "__main__":
    main()
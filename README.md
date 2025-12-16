## Features

### **guppyscreen**
Installs [guppyscreen](https://github.com/ballaswag/guppyscreen) - replaces the screen UI with GuppyScreen. Includes macro to switch back and forth between Creality and GuppyScreen. `gcode_shell_command` is auto-bundled as a dependency.

### **ustreamer**
Disables the stock WebRTC camera feed and installs a ustreamer service that outputs MJPEG on port 8080. Automatically configures the camera in Fluidd.

### **kamp**
Installs [KAMP](https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging) for adaptive purging (NOT meshing).

### **macros**
Helper and nice-to-have macros (`macros.cfg`).

### **start_print**
Overhauled version of Jamin's start print macro (`start_print.cfg`). Automatically installs `macros` as a dependency.

### **overrides**
Overrides configuration (`overrides.cfg`) - empty on branch `main`, contains my full overrides on branch `jac`, which you likely do not want.

### **cleanup**
Adds a service to the printer (accessible via Fluidd's services tab) that deletes all `printer.cfg` backups except the latest 2.

### **shaketune**
Installs [Shaketune](https://github.com/Frix-x/klippain-shaketune).

### **non_critical_carto**
Modifies Klipper files to allow the Cartographer to be disconnected and reconnected without Klipper shutdowns. If homing is attempted while disconnected, it will abort and flag a warning.

### **timelapse / timelapseh264**
Installs [Moonraker Timelapse](https://github.com/mainsail-crew/moonraker-timelapse). Default is `timelapseh264` (H.264 output), while `timelapse` outputs MJPEG.

### **mainsail**
Installs [Mainsail](https://github.com/mainsail-crew/mainsail) on port 4409 alongside Fluidd.

### **gcode_shell_command**
Installs [G-Code Shell Command Extension](https://github.com/dw-0/kiauh/blob/master/docs/gcode_shell_command.md).

---

## Usage

### Option 1: Edit Feature List

Edit the `FEATURES` section in `install.sh` to comment out components you don't want:

```bash
# In install.sh:
FEATURES="
  guppyscreen
  ustreamer
  kamp
  macros
  start_print
# overrides       # <--- Comment out to skip
  cleanup
# shaketune       # <--- Comment out to skip
  non_critical_carto
  timelapse
  mainsail
"
```

Then run:
```bash
./install.sh
```

### Option 2: Use Command-Line Flags
```bash
./install.sh --c kamp overrides cleanup
```

**Available components:** `guppyscreen`, `ustreamer`, `kamp`, `macros`, `start_print`, `overrides`, `cleanup`, `shaketune`, `non_critical_carto`, `timelapse`, `timelapseh264`, `mainsail`, `gcode_shell_command`

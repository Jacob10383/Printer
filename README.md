## Quick Start

1. SSH into your printer
2. Clone this repository: `git clone https://github.com/Jacob10383/Printer`
3. Run the installer: `./install.sh`

## Usage Options

### Install All Components
```bash
 ./install.sh
```

### Dry Run (Preview Changes)
```bash
 ./install.sh --dry-run
```

### Install Specific Components
```bash
 ./install.sh --c kamp overrides cleanup
```

Available components: `ustreamer`, `kamp`, `overrides`, `cleanup`, `resonance`, `bed_mesh`, `timelapse`, `timelapseh264`, `mainsail`

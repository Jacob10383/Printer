#!/bin/sh
# wrapper to call py installer

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Ensure PATH includes /opt/bin for non-interactive shells (wget/unzip/git live here)
export PATH=/opt/bin:/opt/sbin:$PATH

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then
   print_error "This script must be run as root (use sudo)"
   exit 1
fi

# Check if Python 3 is available
if ! command -v python3 > /dev/null 2>&1; then
    print_error "Python 3 is required but not installed"
    exit 1
fi

# Check if install.py exists
if [ ! -f "scripts/install.py" ]; then
    print_error "scripts/install.py not found"
    exit 1
fi

print_status "Python version: $(python3 --version)"

# Make install.py executable
chmod +x scripts/install.py

# Run the installer with all arguments passed through
python3 scripts/install.py "$@"
exit_code=$?

if [ $exit_code -ne 0 ]; then
    print_error "Installation failed!"
fi

exit $exit_code

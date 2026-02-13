#!/bin/sh
#wrapper to call py
set -e

# ============================================================
# FEATURE LIST 
# ============================================================
FEATURES="
  guppyscreen
  ustreamer
  kamp
  macros
  start_print
# overrides
  cleanup
  shaketune
  timelapseh264
  mainsail
"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'


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


parse_features() {
    echo "$FEATURES" | \
        grep -v '^[[:space:]]*#' | \
        grep -v '^[[:space:]]*$' | \
        sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}


export PATH=/opt/bin:/opt/sbin:$PATH


if [ "$(id -u)" -ne 0 ]; then
   print_error "This script must be run as root"
   exit 1
fi


if ! command -v python3 > /dev/null 2>&1; then
    print_error "Python 3 is required but not installed"
    exit 1
fi


if [ ! -f "scripts/install.py" ]; then
    print_error "scripts/install.py not found"
    exit 1
fi

print_status "Python version: $(python3 --version)"


chmod +x scripts/install.py

INSTALL_ARGS=""


has_components_flag=0
for arg in "$@"; do
    case "$arg" in
        --components|--c)
            has_components_flag=1
            break
            ;;
    esac
done


if [ $has_components_flag -eq 0 ]; then
    features=$(parse_features)
    if [ -n "$features" ]; then

        feature_args=$(echo "$features" | tr '\n' ' ')
        INSTALL_ARGS="--components $feature_args"
        print_status "Installing features: $feature_args"
    fi
fi


python3 scripts/install.py $INSTALL_ARGS "$@"
exit_code=$?

if [ $exit_code -ne 0 ]; then
    print_error "Installation failed!"
fi

exit $exit_code

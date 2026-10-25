set positional-arguments

yolo := "false"

default:
    @just --list

# Reconcile declared apps, home, privacy, and stock duplicate removal
setup device:
    @python3 manage.py setup "$1" --yolo {{ yolo }}

# Show drift without changing the device
check device:
    @python3 manage.py check "$1"

# Reconcile pinned app versions, preserving existing app data
update device:
    @python3 manage.py setup "$1" --yolo {{ yolo }}

# Install the configured home apps and apply the grid, dock, and privacy policy
home device:
    @python3 manage.py home "$1" --yolo {{ yolo }}

# Apply the configured notification and runtime permission policy
privacy device:
    @python3 manage.py privacy "$1"

# Switch back to the retained Pixel Launcher
pixel device:
    @python3 manage.py pixel "$1"

# Install and configure one declared app
install device app:
    @python3 manage.py install "$1" "$2" --yolo {{ yolo }}

# Open a managed app
open device app="morphe":
    @python3 manage.py open "$1" "$2"

# Pair using Android's Wireless debugging pairing address; ADB asks for its code
pair device address:
    @python3 manage.py pair "$1" "$2"

# Connect using Android's Wireless debugging connection address
connect device address:
    @python3 manage.py connect "$1" "$2"

# Return to the device's USB transport
usb device:
    @python3 manage.py usb "$1"

# List connected ADB transports
devices:
    @adb devices -l

# Run the offline safety and reconciliation checks
test:
    @python3 -m unittest discover -s tests -v

#!/bin/bash
# Start the neucams GUI on a Linux rig.
#   launch_neucams.sh [preferences.json]
# NEUCAMS_PY  python of the neucams env  (default: ~/code/neucams-venv/bin/python)
# NEUCAMS_PREF  preferences file          (default: first argument, else the a2A1920 preset)
# Only one program may open a camera: close FicTrac / pylon Viewer first.
cd "$(dirname "$(readlink -f "$0")")"
PY="${NEUCAMS_PY:-$HOME/code/neucams-venv/bin/python}"
PREF="${1:-${NEUCAMS_PREF:-neucams/jsonfiles/basler_a2A1920.json}}"
echo "neucams $(git log --oneline -1 2>/dev/null)  prefs: $PREF"
"$PY" -m neucams -p "$PREF"
echo "neucams closed. Press ENTER to close this window..."
read

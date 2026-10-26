#!/bin/bash
# Start/stop neucams recordings from 2p frame pulses on the rig Arduino Due.
#   launch_due_trigger.sh [preferences.json]
# Order: 1) neucams GUI open (it is driven over UDP)  2) this  3) start the 2p.
# Uses the same preferences as launch_neucams.sh (~/neucams/default.json if present).
cd "$(dirname "$(readlink -f "$0")")"
PY="${NEUCAMS_PY:-$HOME/code/neucams-venv/bin/python}"
DEFAULT="$HOME/neucams/default.json"
[ -f "$DEFAULT" ] || DEFAULT=neucams/jsonfiles/basler_a2A1920.json
PREF="${1:-${NEUCAMS_PREF:-$DEFAULT}}"
echo "2p trigger for neucams  ($(git log --oneline -1 2>/dev/null))  prefs: $PREF"
echo "Make sure the neucams window is open. Ctrl+C here to stop."
"$PY" -m neucams.due_trigger -p "$PREF"
echo "2p trigger closed. Press ENTER to close this window..."
read

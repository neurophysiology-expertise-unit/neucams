"""Start/stop neucams recordings from 2p frame pulses read on the rig Arduino Due.

    python -m neucams.due_trigger -p neucams/jsonfiles/basler_a2A1920.json

Runs next to the neucams GUI and drives it over its UDP commands
(folder= / start / stop). The Due (fictrac-arduino-loop firmware) reports each
2p frame pulse on shield In1 as "@I_<arduino ms>_<count>". The first pulse starts
a run; no pulse for --stop-after seconds stops it; the next pulse starts a new run.
During a run every Due line is logged, with PC time, to due_events.csv next to
the video (<data_folder>/<camera description>/<run>/).

Sync: PC time links pulses to the run, not to single camera frames. For
frame-level alignment wire the camera's exposure output to a Due input and log
it here too (the lines are logged whatever their type).
"""
import argparse
import csv
import datetime
import json
import os
import socket
import time

import serial


def parse(line):
    """'@I_15454_1' -> ('I', 15454, 1); missing fields -> None."""
    parts = line[1:].split('_')
    nums = [int(p) if p.lstrip('-').isdigit() else None for p in parts[1:3]]
    return (parts[0], *(nums + [None, None])[:2])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('-p', '--pref', required=True, help='neucams preferences JSON (as given to the GUI)')
    ap.add_argument('--port', default='/dev/ttyACM0')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--stop-after', type=float, default=1.0,
                    help='seconds without a 2p pulse that end a run (default 1.0)')
    ap.add_argument('--boot-wait', type=float, default=4.0,
                    help='seconds to wait after opening the port (the Due reboots; default 4)')
    a = ap.parse_args()

    prefs = json.load(open(a.pref))
    data_folder = prefs['recorder_params']['data_folder']
    cam = prefs['cams'][0].get('description', 'camera')
    udp = ('127.0.0.1', int(prefs.get('server_params', {}).get('server_port', 23160)))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)

    def send(msg):
        sock.sendto(msg.encode(), udp)
        try:
            reply = sock.recvfrom(256)[0].decode()
        except socket.timeout:
            reply = '(no reply: is the neucams GUI running with udp_enable?)'
        print(f"  neucams <- {msg!r}: {reply}")

    ser = serial.Serial(a.port, a.baud, timeout=0.05)
    print(f"Due on {a.port}; waiting {a.boot_wait:.0f} s for its reboot...")
    time.sleep(a.boot_wait)
    ser.reset_input_buffer()
    ser.write(b'@S\n')
    print(f"Waiting for 2p pulses (@I). A run stops after {a.stop_after} s without one. Ctrl+C to quit.")

    run = log = writer = None
    last_pulse = n_pulses = 0
    try:
        while True:
            line = ser.readline().decode('ascii', 'ignore').strip()
            now = time.time()
            if line.startswith('@'):
                kind, ms, count = parse(line)
                if kind == 'I' and run is None:
                    run = datetime.datetime.now().strftime('%y%m%d_%H%M%S')
                    folder = os.path.join(data_folder, cam, run)
                    os.makedirs(folder, exist_ok=True)
                    log = open(os.path.join(folder, 'due_events.csv'), 'w', newline='')
                    writer = csv.writer(log)
                    writer.writerow(['pc_time_s', 'raw', 'type', 'arduino_ms', 'count'])
                    print(f"2p started -> run {run}")
                    send(f'folder={run}')
                    send('start')
                    n_pulses = 0
                if run is not None:
                    writer.writerow([f'{now:.6f}', line, kind, ms, count])
                    if kind == 'I':
                        last_pulse = now
                        n_pulses += 1
            if run is not None and now - last_pulse > a.stop_after:
                send('stop')
                log.close()
                print(f"2p stopped -> run {run} closed ({n_pulses} pulse messages)")
                run = None
    except KeyboardInterrupt:
        print('\nquitting')
    finally:
        if run is not None:
            send('stop')
            log.close()
        try:
            ser.write(b'@Z\n')  # stop the Due's timers; fails only if the port is already gone
        except serial.SerialException:
            pass
        ser.close()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
One-click launcher for the D&D character sheet.

Starts server.py in the background, waits until it's actually
responding, then opens the character sheet in your default browser.
Leave this window open while you play -- closing it (or Ctrl+C) stops
the server too.

Put this file in the same folder as server.py and character_sheet.html.

Usage:
    python3 launch.py
"""
import http.client
import os
import subprocess
import sys
import time
import webbrowser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(SCRIPT_DIR, 'server.py')
URL = 'http://localhost:8765/character_sheet.html'
STATUS_URL = 'http://localhost:8765/status.html'
PING_HOST = 'localhost'
PING_PORT = 8765
PING_PATH = '/api/ping'
STARTUP_TIMEOUT = 10  # seconds to wait for the server before giving up


def server_is_up():
    conn = None
    try:
        conn = http.client.HTTPConnection(PING_HOST, PING_PORT, timeout=0.5)
        conn.request('GET', PING_PATH)
        resp = conn.getresponse()
        return resp.status == 200
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def main():
    if not os.path.exists(SERVER_SCRIPT):
        print(f"Could not find server.py next to this script at: {SERVER_SCRIPT}")
        sys.exit(1)

    if server_is_up():
        print("Server is already running -- reusing it.")
        proc = None
    else:
        print("Starting local server...")
        proc = subprocess.Popen([sys.executable, SERVER_SCRIPT])

        waited = 0.0
        while not server_is_up():
            if proc.poll() is not None:
                print("Server process exited before it came up -- check the output above.")
                sys.exit(1)
            time.sleep(0.25)
            waited += 0.25
            if waited >= STARTUP_TIMEOUT:
                print("Server didn't respond in time; opening the page anyway.")
                break

    print(f"Opening {URL}")
    webbrowser.open(URL)

    print(f"Opening status tab: {STATUS_URL}")
    webbrowser.open_new_tab(STATUS_URL)

    if proc is None:
        print("Done -- this launcher isn't managing the server, so it will exit now.")
        return

    print("Server is running. Press Ctrl+C here to stop it.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == '__main__':
    main()

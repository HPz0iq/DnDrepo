#!/usr/bin/env python3
"""
Local companion server for the D&D character sheet.

Run this once, then open the sheet through it (not by double-clicking
the HTML file). While it's running, the sheet autosaves every few
seconds AND on every manual "Save to file" click -- both write to the
same file, saves/Campaign_current.json, so this never piles up dozens
of save files. The previous version is kept as
saves/Campaign_current.backup.json (one generation back) as a safety
net. Every save is written to a temp file, verified, and only then
atomically swapped into place, so a failed/partial write can never
corrupt your save.

Every time the sheet loads, it also snapshots the Diary tab into
saves/Diary_text.txt. Each snapshot is written to the TOP of that file,
tagged with #YYYY-MM-DD for the day it was saved, so the file grows
into a running log of the diary over time.

Usage:
    python3 server.py

Then open in your browser:
    http://localhost:8765/character_sheet.html
"""
import http.server
import json
import os
import re
import shutil
import socketserver
import time
from datetime import datetime

PORT = 8765
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAVES_DIR = os.path.join(SCRIPT_DIR, 'saves')
CURRENT_SAVE_NAME = 'Campaign_current.json'
BACKUP_SAVE_NAME = 'Campaign_current.backup.json'
START_TIME = time.time()


class Handler(http.server.SimpleHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/ping':
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        if self.path == '/api/status':
            self._handle_status()
            return
        if self.path == '/api/latest-save':
            self._handle_latest_save()
            return
        super().do_GET()

    def _handle_latest_save(self):
        try:
            os.makedirs(SAVES_DIR, exist_ok=True)
            current_path = os.path.join(SAVES_DIR, CURRENT_SAVE_NAME)

            if os.path.exists(current_path):
                with open(current_path, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                self._respond_json(200, {'ok': True, 'filename': CURRENT_SAVE_NAME, 'content': content})
                return

            # No autosave file yet -- fall back to the old numbered-save
            # scheme, in case this is an existing campaign folder from
            # before autosave existed.
            legacy_files = [f for f in os.listdir(SAVES_DIR)
                            if f.startswith('Campaign_') and f.endswith('.json')
                            and f not in (CURRENT_SAVE_NAME, BACKUP_SAVE_NAME)]
            if not legacy_files:
                self._respond_json(200, {'ok': False, 'reason': 'no saves found yet'})
                return

            newest = max(legacy_files,
                         key=lambda f: os.path.getmtime(os.path.join(SAVES_DIR, f)))
            with open(os.path.join(SAVES_DIR, newest), 'r', encoding='utf-8') as fh:
                content = fh.read()

            self._respond_json(200, {'ok': True, 'filename': newest, 'content': content})
        except Exception as e:
            self._respond_json(500, {'ok': False, 'error': str(e)})

    def _handle_status(self):
        try:
            os.makedirs(SAVES_DIR, exist_ok=True)
            current_path = os.path.join(SAVES_DIR, CURRENT_SAVE_NAME)
            current_exists = os.path.exists(current_path)
            current_last_modified = None
            if current_exists:
                current_last_modified = datetime.fromtimestamp(
                    os.path.getmtime(current_path)
                ).isoformat(timespec='seconds')

            legacy_files = [f for f in os.listdir(SAVES_DIR)
                            if f.startswith('Campaign_') and f.endswith('.json')
                            and f not in (CURRENT_SAVE_NAME, BACKUP_SAVE_NAME)]

            diary_path = os.path.join(SAVES_DIR, 'Diary_text.txt')
            diary_exists = os.path.exists(diary_path)
            diary_last_modified = None
            if diary_exists:
                diary_last_modified = datetime.fromtimestamp(
                    os.path.getmtime(diary_path)
                ).isoformat(timespec='seconds')

            self._respond_json(200, {
                'ok': True,
                'port': PORT,
                'script_dir': SCRIPT_DIR,
                'saves_dir': SAVES_DIR,
                'uptime_seconds': round(time.time() - START_TIME),
                'current_save_exists': current_exists,
                'current_save_last_modified': current_last_modified,
                'legacy_save_count': len(legacy_files),
                'diary_exists': diary_exists,
                'diary_last_modified': diary_last_modified,
            })
        except Exception as e:
            self._respond_json(500, {'ok': False, 'error': str(e)})

    def do_POST(self):
        if self.path == '/api/save':
            self._handle_save()
        elif self.path == '/api/diary':
            self._handle_diary()
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        return json.loads(raw)

    def _respond_json(self, status, obj):
        self.send_response(status)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode('utf-8'))

    def _handle_save(self):
        tmp_path = None
        try:
            payload = self._read_json_body()
            content = payload.get('content', '{}')

            os.makedirs(SAVES_DIR, exist_ok=True)
            current_path = os.path.join(SAVES_DIR, CURRENT_SAVE_NAME)
            backup_path = os.path.join(SAVES_DIR, BACKUP_SAVE_NAME)
            tmp_path = current_path + '.tmp'

            # Roll whatever is currently saved into the single backup slot
            # before writing the new version, so one bad write can never
            # wipe out both the current save and its backup at once.
            if os.path.exists(current_path):
                try:
                    shutil.copyfile(current_path, backup_path)
                except Exception:
                    pass  # backup is best-effort; don't let it block the real save

            # Write to a temp file first and verify it actually parses as
            # valid JSON before touching the real save file. Only a file
            # that passes this check ever gets swapped into place.
            with open(tmp_path, 'w', encoding='utf-8') as fh:
                fh.write(content)
            with open(tmp_path, 'r', encoding='utf-8') as fh:
                written = fh.read()
            json.loads(written)  # raises ValueError if the write is truncated/corrupt

            os.replace(tmp_path, current_path)  # atomic swap on POSIX and Windows
            tmp_path = None  # consumed by the replace -- nothing left to clean up

            self._respond_json(200, {'ok': True, 'filename': CURRENT_SAVE_NAME})
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            self._respond_json(500, {'ok': False, 'error': str(e)})

    def _handle_diary(self):
        """Prepend a dated snapshot of the diary text to saves/Diary_text.txt."""
        try:
            payload = self._read_json_body()
            date_str = re.sub(r'[^0-9A-Za-z_-]', '', payload.get('date', 'save'))
            text = payload.get('text', '')

            os.makedirs(SAVES_DIR, exist_ok=True)
            diary_path = os.path.join(SAVES_DIR, 'Diary_text.txt')

            existing = ''
            if os.path.exists(diary_path):
                with open(diary_path, 'r', encoding='utf-8') as fh:
                    existing = fh.read()

            entry = f'#{date_str}\n{text}\n\n'
            with open(diary_path, 'w', encoding='utf-8') as fh:
                fh.write(entry + existing)

            self._respond_json(200, {'ok': True, 'filename': 'Diary_text.txt'})
        except Exception as e:
            self._respond_json(500, {'ok': False, 'error': str(e)})


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == '__main__':
    os.chdir(SCRIPT_DIR)
    with ReusableTCPServer(("", PORT), Handler) as httpd:
        print(f"Serving this folder at http://localhost:{PORT}/")
        print(f"Open this in your browser: http://localhost:{PORT}/character_sheet.html")
        print(f"Autosaving to: {os.path.join(SAVES_DIR, CURRENT_SAVE_NAME)}")
        print(f"One-generation backup kept at: {os.path.join(SAVES_DIR, BACKUP_SAVE_NAME)}")
        print(f"Diary log will be kept at: {os.path.join(SAVES_DIR, 'Diary_text.txt')}")
        print("Press Ctrl+C to stop.")
        httpd.serve_forever()

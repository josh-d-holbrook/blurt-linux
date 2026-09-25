#!/usr/bin/python3
"""Native GTK/X11 dictation tray for Debian. See README.md."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import signal
import sqlite3
import wave

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Secret", "1")
from gi.repository import Gtk, Gdk, Gio, GLib, Secret, AyatanaAppIndicator3
from Xlib import X, XK, display, error

from core import DictationError, KeyGesture, LiveTranscriber, MAX_SECONDS, ModifierGesture, RATE, Recorder, make_config, transcribe
from recovery import RecoveryStore
from appearance import CompactOverlay, apply_theme

APP_ID = "local.blurtlinux.Dictation"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "blurt-linux"
CONFIG_FILE = CONFIG_DIR / "settings.json"
SCHEMA = Secret.Schema.new(APP_ID, Secret.SchemaFlags.NONE,
                          {"service": Secret.SchemaAttributeType.STRING})
ATTRS = {"service": "assemblyai-dictation"}
DEFAULTS = dict(hotkey="Super_R", source="prefer-brio", polished=True, auto_paste=True,
                restore_clipboard=True, keyterms="", instruction="", language="en")
KEY_LABELS = {"Super_R": "Right Super (Command / Windows)", "Alt_R": "Right Alt (Option)", "Control_R": "Right Control",
              "F8": "F8 (may require Fn)", "F9": "F9", "F10": "F10", "F12": "F12",
              "Pause": "Pause", "Scroll_Lock": "Scroll Lock"}


def load_settings():
    result = DEFAULTS.copy()
    try:
        data = json.loads(CONFIG_FILE.read_text())
        for k, v in DEFAULTS.items():
            if k in data and type(data[k]) is type(v):
                result[k] = data[k]
    except (OSError, ValueError, TypeError):
        pass
    return result


def save_settings(settings):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = CONFIG_FILE.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(settings, out, indent=2)
    os.replace(temp, CONFIG_FILE)


def read_key():
    override = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if override:
        return override
    try:
        return Secret.password_lookup_sync(SCHEMA, ATTRS, None) or ""
    except GLib.Error:
        return ""


class Hotkey:
    def __init__(self, callback, state_provider=lambda: "idle"):
        self.d = display.Display()
        self.root = self.d.screen().root
        self.callback = callback
        self.keycode = None
        self.modifier_mode = False
        self.modifier_gesture = ModifierGesture()
        self.state_provider = state_provider
        self.escape = self.d.keysym_to_keycode(XK.string_to_keysym("Escape"))
        self.releases = {}
        self.escape_active = False
        self.masks = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
        self.source = GLib.timeout_add(15, self.poll)

    def grab(self, code):
        failed = []
        for mask in self.masks:
            self.root.grab_key(code, mask, False, X.GrabModeAsync, X.GrabModeAsync,
                               onerror=lambda *args: (failed.append(True), True)[1])
        self.d.sync()
        if failed:
            self.ungrab(code)
            raise DictationError("That shortcut is already in use. Choose another key.")

    def ungrab(self, code):
        for mask in self.masks:
            self.root.ungrab_key(code, mask)
        self.d.sync()

    def bind(self, name):
        if name not in KEY_LABELS:
            raise DictationError("Unsupported shortcut.")
        code = self.d.keysym_to_keycode(XK.string_to_keysym(name))
        if not code:
            raise DictationError("That key is not available in the current keyboard layout.")
        if code == self.keycode:
            return
        modifier_mode = name in ("Super_R", "Alt_R", "Control_R")
        if not modifier_mode:
            self.grab(code)
        if self.keycode and not self.modifier_mode:
            self.ungrab(self.keycode)
        self.keycode = code
        self.modifier_mode = modifier_mode
        self.modifier_gesture = ModifierGesture()

    def enable_escape(self, enabled):
        if enabled == self.escape_active:
            return
        if enabled:
            try:
                self.grab(self.escape)
            except DictationError:
                return
        else:
            self.ungrab(self.escape)
        self.escape_active = enabled

    def poll(self):
        if self.modifier_mode:
            # Query physical state, without grabbing/swallowing a modifier or
            # decoding normal typing. Only this gesture's state is retained.
            keys = self.d.query_keymap()
            down = bool(keys[self.keycode // 8] & (1 << (self.keycode % 8)))
            other = any(byte & ~(1 << (self.keycode % 8)) if i == self.keycode // 8 else byte
                        for i, byte in enumerate(keys))
            if down:
                other = other or bool(self.root.query_pointer().mask &
                                      (X.Button1Mask | X.Button2Mask | X.Button3Mask))
            action = self.modifier_gesture.update(down, bool(other), time.monotonic(), self.state_provider())
            if action:
                self.callback(action)
        while self.d.pending_events():
            event = self.d.next_event()
            if event.type not in (X.KeyPress, X.KeyRelease):
                continue
            code = event.detail
            if code not in (self.keycode, self.escape):
                continue
            if event.type == X.KeyPress:
                if code in self.releases:
                    GLib.source_remove(self.releases.pop(code))
                    continue  # X11 auto-repeat release/press pair
                self.callback("cancel" if code == self.escape else "press")
            elif code != self.escape:
                self.releases[code] = GLib.timeout_add(35, self.release, code)
        return True

    def release(self, code):
        self.releases.pop(code, None)
        self.callback("release")
        return False

    def focus(self):
        try:
            prop = self.root.get_full_property(self.d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
            if not prop or not prop.value[0]:
                return None
            wid = int(prop.value[0])
            w = self.d.create_resource_object("window", wid)
            cls = " ".join(w.get_wm_class() or ()).lower()
            return wid, cls
        except (error.XError, AttributeError):
            return None

    def close(self):
        GLib.source_remove(self.source)
        for pending in self.releases.values():
            GLib.source_remove(pending)
        self.d.close()


class Blurt(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.settings = load_settings()
        self.key = ""
        self.state = "idle"
        self.recorder = None
        self.gesture = KeyGesture()
        self.history = []
        self.target = None
        self.generation = 0
        self.paste_pending = False
        self.last_audio = None
        self.store = None
        self.active_sid = None
        self.pending_jobs = set()
        self.emergency_audio = {}
        self.emergency_text = {}
        self.mic_test = None
        self.mic_test_until = 0
        self.window = None
        self.hotkey = None
        self.started = False
        self.quitting = False
        self.connect("activate", self.activate_app)
        self.connect("shutdown", self.shutdown_app)

    def activate_app(self, *_):
        if self.started:
            self.show_window()
            return
        self.started = True
        self.hold()
        self.key = read_key()
        storage_error = None
        recovered = 0
        try:
            self.store = RecoveryStore()
            recovered = self.store.recover_interrupted()
        except (OSError, sqlite3.Error) as exc:
            self.store = None
            storage_error = "Recovery storage unavailable. Dictation is disabled until storage is working."
        self.apply_theme()
        self.build_window()
        self.build_overlay()
        self.build_tray()
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        try:
            self.hotkey = Hotkey(self.on_key, self.gesture_state)
            self.hotkey.bind(self.settings["hotkey"])
        except Exception as exc:
            self.status.set_text("Shortcut unavailable: " + str(exc))
            self.show_window()
        GLib.timeout_add(60, self.update_meter)
        self.cleanup_history()
        GLib.timeout_add_seconds(3600, self.cleanup_history)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self.request_quit)
        self.add_action(quit_action)
        if storage_error:
            self.status.set_text(storage_error)
            self.show_window()
        elif recovered:
            self.status.set_text(f"Recovered {recovered} interrupted recording(s). Select one in History to retry.")
            self.show_window()
        if not self.key:
            self.status.set_text("Add your AssemblyAI API key to get started.")
            self.show_window()

    def build_window(self):
        self.window = Gtk.ApplicationWindow(application=self, title="Blurt Linux")
        self.window.set_name("blurt-main")
        self.window.set_default_size(-1, -1)
        header = Gtk.HeaderBar(title="Blurt", show_close_button=True)
        header.set_name("blurt-header")
        self.window.set_titlebar(header)
        self.window.connect("delete-event", lambda w, e: (w.hide(), True)[1])
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=22)
        self.window.add(outer)
        title = Gtk.Label(xalign=0)
        title.set_markup('<span size="30000" weight="heavy" foreground="#8ddbaa">blurt</span>  <span size="13000" foreground="#a2afa5">for Linux</span>')
        outer.pack_start(title, False, False, 0)
        self.help = Gtk.Label(xalign=0)
        self.help.set_text(self.help_text())
        outer.pack_start(self.help, False, False, 0)
        self.status = Gtk.Label(label="Ready", xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(65)
        self.status.set_selectable(True)
        outer.pack_start(self.status, False, False, 0)
        book = Gtk.Notebook()
        outer.pack_start(book, True, True, 0)
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        settings_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        settings_page.pack_start(settings, True, True, 0)
        book.append_page(settings_page, Gtk.Label(label="Settings"))
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        settings.pack_start(grid, False, False, 0)
        row = 0

        def field(label, widget):
            nonlocal row
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            row += 1
            return widget

        self.key_entry = field("API key", Gtk.Entry())
        self.key_entry.set_visibility(False)
        self.key_entry.set_placeholder_text("Saved in your keyring" if self.key else "Paste your AssemblyAI key")
        self.key_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.key_entry.set_tooltip_text("Leave blank to keep the saved key. Never stored in settings.json.")
        self.key_combo = field("Shortcut", Gtk.ComboBoxText())
        for name, label in KEY_LABELS.items():
            self.key_combo.append(name, label)
        self.key_combo.set_active_id(self.settings["hotkey"])
        self.mic_combo = field("Microphone", Gtk.ComboBoxText())
        self.mic_combo.append("prefer-brio", "Brio when connected · otherwise system default")
        self.mic_combo.append("", "System default")
        try:
            sources = json.loads(subprocess.check_output(["pactl", "--format=json", "list", "sources"], timeout=3))
            for source in sources:
                if not source["name"].endswith(".monitor"):
                    self.mic_combo.append(source["name"], source.get("description", source["name"]))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        self.mic_combo.set_active_id(self.settings["source"])
        if self.mic_combo.get_active() < 0:
            self.mic_combo.set_active(0)
        self.language_combo = field("Language", Gtk.ComboBoxText())
        for code, name in [("en","English"),("es","Spanish"),("de","German"),("fr","French"),
                           ("it","Italian"),("pt","Portuguese"),("tr","Turkish"),("nl","Dutch"),
                           ("sv","Swedish"),("no","Norwegian"),("da","Danish"),("fi","Finnish"),
                           ("hi","Hindi"),("vi","Vietnamese"),("ar","Arabic"),("he","Hebrew"),
                           ("ja","Japanese"),("ur","Urdu"),("zh","Chinese")]:
            self.language_combo.append(code, name)
        self.language_combo.set_active_id(self.settings["language"])
        self.terms_entry = field("Key terms", Gtk.Entry())
        self.terms_entry.set_text(self.settings["keyterms"])
        self.terms_entry.set_placeholder_text("Names and jargon, separated by commas")
        self.style_entry = field("Output style", Gtk.Entry())
        self.style_entry.set_text(self.settings["instruction"])
        self.style_entry.set_placeholder_text("Optional, e.g. use lowercase")
        self.checks = {}
        for key, label in [("polished","Use polished text"),("auto_paste","Paste automatically into the original app"),
                           ("restore_clipboard","Restore previous text clipboard after pasting")]:
            check = Gtk.CheckButton(label=label)
            check.set_active(self.settings[key])
            settings.pack_start(check, False, False, 0)
            self.checks[key] = check
        mic_button = Gtk.Button(label="Check microphone")
        mic_button.connect("clicked", self.check_microphone)
        settings.pack_start(mic_button, False, False, 0)
        self.mic_info = Gtk.Label(label="Voice bars show input activity; a countdown appears for the final 20 seconds.", xalign=0)
        self.mic_info.set_line_wrap(True)
        self.mic_info.set_max_width_chars(60)
        settings.pack_start(self.mic_info, False, False, 0)
        save = Gtk.Button(label="Save settings")
        save.get_style_context().add_class("suggested-action")
        save.connect("clicked", self.save)
        save.set_margin_start(16)
        save.set_margin_end(16)
        save.set_margin_top(8)
        save.set_margin_bottom(12)
        settings_page.pack_end(save, False, False, 0)
        recent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        self.book = book
        book.append_page(recent, Gtk.Label(label="History"))
        history_top = Gtk.Box(spacing=10)
        history_top.pack_start(Gtk.Label(label="Local recovery · automatic expiry", xalign=0), True, True, 0)
        self.show_trash = Gtk.CheckButton(label="Trash")
        self.show_trash.connect("toggled", lambda *_: self.refresh_history())
        history_top.pack_end(self.show_trash, False, False, 0)
        recent.pack_start(history_top, False, False, 0)
        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_max_width_chars(60)
        note.set_text("Local recovery: completed audio 48 hours; unfinished audio and transcripts 7 days. Audio goes to AssemblyAI when you transcribe or retry. Esc pauses and keeps your recording.")
        recent.pack_start(note, False, False, 0)
        self.history_model = Gtk.ListStore(str, str, str, str)
        self.history_list = Gtk.TreeView(model=self.history_model)
        for i, label in enumerate(("Recorded", "Length", "Status"), 1):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(label, renderer, text=i)
            column.set_expand(i == 1)
            self.history_list.append_column(column)
        self.history_list.get_selection().connect("changed", self.history_selected)
        listing = Gtk.ScrolledWindow()
        listing.set_size_request(-1, 140)
        listing.add(self.history_list)
        recent.pack_start(listing, True, True, 0)
        self.history_view = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.history_view.set_left_margin(8)
        self.history_view.set_right_margin(8)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.history_view)
        recent.pack_start(scroll, True, True, 0)
        scroll.set_size_request(-1, 125)
        actions = Gtk.Box(spacing=8)
        for label, callback in [("Copy text", self.copy_selected), ("Retry", self.retry),
                                ("Export audio", self.export_audio), ("Trash / Restore", self.trash_selected)]:
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            actions.pack_start(button, True, True, 0)
        recent.pack_start(actions, False, False, 0)
        self.delete_button = Gtk.Button(label="Delete selected recording permanently…")
        self.delete_button.connect("clicked", self.delete_selected)
        self.delete_button.set_sensitive(False)
        recent.pack_start(self.delete_button, False, False, 0)
        footer = Gtk.Label(label="Close this window to keep Blurt in the tray.", xalign=0)
        outer.pack_start(footer, False, False, 0)

    def help_text(self):
        key = KEY_LABELS.get(self.settings['hotkey'], self.settings['hotkey'])
        return f"Tap {key} to start and stop, or hold to talk. Esc pauses and saves."

    def apply_theme(self):
        apply_theme()

    def build_overlay(self):
        self.overlay = CompactOverlay()

    def update_recording_display(self, audio_seconds, power_db):
        self.overlay.recording(audio_seconds, power_db)

    def build_tray(self):
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "blurt-linux", "audio-input-microphone", AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Blurt Linux")
        menu = Gtk.Menu()
        self.state_item = Gtk.MenuItem(label="Ready · " + KEY_LABELS.get(self.settings["hotkey"], self.settings["hotkey"]))
        self.state_item.set_sensitive(False)
        menu.append(self.state_item)
        for label, callback in [("Stop dictation", lambda *_: self.stop()),
                                ("Pause and keep recording", lambda *_: self.cancel()),
                                ("Settings and History", lambda *_: self.show_window()),
                                ("Copy latest", self.copy_latest), ("Quit", self.request_quit)]:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)
        menu.show_all()
        self.indicator.set_menu(menu)

    def show_window(self):
        self.window.show_all()
        # Use GTK's actual content minimum, including fonts and display scaling.
        self.window.resize(1, 1)  # GTK clamps to the actual minimum, including decorations.
        self.window.present()

    def save(self, *_):
        if self.state != "idle":
            self.status.set_text("Finish this dictation before changing settings.")
            return
        settings = {**self.settings, "hotkey": self.key_combo.get_active_id(),
                    "source": self.mic_combo.get_active_id() or "",
                    "language": self.language_combo.get_active_id() or "en",
                    "keyterms": self.terms_entry.get_text(), "instruction": self.style_entry.get_text()}
        settings.update({k: c.get_active() for k, c in self.checks.items()})
        old_hotkey = self.settings["hotkey"]
        try:
            make_config(settings)
            if not self.hotkey:
                self.hotkey = Hotkey(self.on_key, self.gesture_state)
            self.hotkey.bind(settings["hotkey"])
            key = self.key_entry.get_text().strip()
            if key:
                if any(ord(c) < 33 or ord(c) > 126 for c in key):
                    raise DictationError("The API key contains spaces or invalid characters.")
                stored = Secret.password_store_sync(SCHEMA, ATTRS, Secret.COLLECTION_DEFAULT,
                                                    "Blurt Linux — AssemblyAI API key", key, None)
                if not stored:
                    raise DictationError("The desktop keyring could not save the key.")
                self.key = key
                self.key_entry.set_text("")
                self.key_entry.set_placeholder_text("Saved in your keyring")
            save_settings(settings)
            self.settings = settings
            self.help.set_text(self.help_text())
            self.set_state("idle", "Settings saved. " + self.help_text())
        except (DictationError, GLib.Error, OSError) as exc:
            if self.hotkey:
                self.hotkey.bind(old_hotkey)
            self.status.set_text("Could not save: " + str(exc))

    def on_key(self, action):
        if action == "cancel":
            self.cancel()
            return
        if action == "start":
            self.start()
            return
        if action == "stop":
            self.stop()
            return
        now = time.monotonic()
        state = "busy" if self.paste_pending else self.state
        result = self.gesture.press(now, state) if action == "press" else self.gesture.release(now, state)
        if result == "start":
            self.start()
        elif result == "stop":
            self.stop()

    def gesture_state(self):
        return "busy" if self.paste_pending else self.state

    def set_state(self, state, message):
        self.state = state
        self.status.set_text(message)
        self.state_item.set_label(message if state != "idle" else "Ready · " + KEY_LABELS.get(self.settings["hotkey"], self.settings["hotkey"]))
        self.indicator.set_icon_full("media-record" if state == "recording" else "audio-input-microphone", message)
        if self.hotkey:
            self.hotkey.enable_escape(state != "idle")
        if state == "idle":
            self.overlay.hide()
        else:
            if state == "recording":
                self.overlay.recording(0)
            else:
                self.overlay.processing("Saving" if state == "stopping" else "Transcribing")
            self.overlay.present_pill()

    def start(self):
        if self.state != "idle" or self.paste_pending:
            return
        if not self.store:
            self.status.set_text("Recovery storage is unavailable. Please fix storage before recording.")
            self.show_window()
            return
        if not self.key:
            self.status.set_text("Add your AssemblyAI API key and click Save settings.")
            self.show_window()
            return
        self.end_mic_test()
        self.generation += 1
        self.target = self.hotkey.focus() if self.hotkey else None
        journal = None
        try:
            journal = self.store.begin(self.settings)
            self.active_sid = journal.session_id
            sid = journal.session_id
            live = LiveTranscriber(self.key, self.settings)
            self.recorder = Recorder(self.settings['source'], on_limit=lambda: GLib.idle_add(self.stop_if_current, sid), journal=journal, live=live)
            self.recorder.start()
            self.started_at = time.monotonic()
            self.set_state('recording', 'Listening — audio is being saved locally.')
            self.refresh_history(self.active_sid)
        except (OSError, sqlite3.Error, DictationError) as exc:
            if journal:
                try:
                    journal.close()
                except OSError:
                    pass
                self.safe_update(journal.session_id, 'failed', 'Could not start microphone capture.')
            self.recorder = None
            self.active_sid = None
            self.set_state('idle', 'Could not start safely. Check the microphone and available disk space.')
            self.show_window()

    def update_meter(self):
        if self.state == 'recording' and self.recorder:
            audio_seconds = len(self.recorder.data) / (RATE * 2)
            self.update_recording_display(audio_seconds, self.recorder.rms_db)
            if self.recorder.error:
                self.stop()
        if self.mic_test:
            rec = self.mic_test
            if rec.error or time.monotonic() >= self.mic_test_until:
                self.end_mic_test()
            else:
                if not rec.data:
                    message = 'Connecting to microphone…'
                elif rec.peak >= .98:
                    message = 'Near clipping — lower the input gain or move farther away.'
                elif rec.rms_db < -55:
                    message = 'Very quiet — speak, or check that the correct microphone is selected.'
                elif rec.rms_db < -35:
                    message = 'Quiet input — move closer if your speech stays here.'
                else:
                    message = 'Input detected — normal meter movement while speaking.'
                self.mic_info.set_text(message)
        return not self.quitting

    def safe_update(self, sid, state, error=''):
        try:
            self.store.update(sid, state, error)
            return True
        except (OSError, sqlite3.Error):
            return False

    def stop(self):
        if self.state != 'recording':
            return False
        recorder, self.recorder = self.recorder, None
        sid = self.active_sid
        self.set_state('transcribing', 'Transcribing — your audio is saved.')
        self.run_job(sid, recorder=recorder)
        return False

    def stop_if_current(self, sid):
        if self.active_sid == sid:
            self.stop()
        return False

    def run_job(self, sid, recorder=None, pcm=None):
        generation = self.generation
        settings, key = self.settings.copy(), self.key
        self.pending_jobs.add(sid)
        def work():
            audio, result, failure = pcm, None, None
            try:
                if recorder:
                    try:
                        audio = recorder.stop()
                    except Exception:
                        audio = bytes(recorder.data)
                        raise
                self.store.update(sid, 'transcribing')
                result = recorder.live.finish() if recorder and recorder.live else transcribe(audio, key, settings)
                if result.text:
                    self.store.complete(sid, result)
                else:
                    self.store.update(sid, 'no_speech', 'No speech detected. Audio is available to retry or export.')
            except Exception as exc:
                if recorder and recorder.live:
                    recorder.live.cancel()
                failure = str(exc) if isinstance(exc, DictationError) else 'Could not complete or save this dictation. Your captured audio is available in History.'
                self.safe_update(sid, 'failed', failure)
            # Database persistence happens before this callback, even if Esc
            # detached the request or a new dictation has started meanwhile.
            GLib.idle_add(self.finish, sid, generation, result, failure, audio)
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.state in ('idle', 'stopping'):
            return
        self.generation += 1
        generation = self.generation
        sid = self.active_sid
        recorder, self.recorder = self.recorder, None
        self.active_sid = None
        if recorder:
            if recorder.live:
                recorder.live.cancel()
            self.set_state('stopping', 'Saving paused recording…')
            def pause():
                problem = None
                try:
                    recorder.stop()
                except Exception as exc:
                    problem = str(exc)
                self.safe_update(sid, 'paused', problem or 'Paused without transcription. Retry or export from History.')
                GLib.idle_add(self.paused, sid, generation, bytes(recorder.data), problem)
            threading.Thread(target=pause, daemon=True).start()
        else:
            # The in-flight response is still saved; Esc only suppresses paste.
            self.set_state('idle', 'Result will be kept in History. Nothing will be pasted.')

    def paused(self, sid, generation, pcm, problem):
        if pcm and (problem or not self.store.path(sid).exists()):
            self.emergency_audio[sid] = pcm
        self.refresh_history(sid)
        if generation == self.generation and not self.quitting:
            self.set_state('idle', 'Paused — recording kept in History. Use Retry to transcribe it.' if not problem else problem)
        return False

    def finish(self, sid, generation, result, failure, pcm):
        self.pending_jobs.discard(sid)
        if failure and pcm:
            try:
                complete_audio = self.store.path(sid).stat().st_size >= len(pcm)
            except OSError:
                complete_audio = False
            if not complete_audio:
                self.emergency_audio[sid] = pcm
        if result and result.text and failure:
            self.emergency_text[sid] = result.text
        if generation != self.generation or self.quitting:
            self.refresh_history(sid)
            return False
        self.active_sid = None
        if failure:
            self.refresh_history(sid)
            self.set_state('idle', failure)
            self.book.set_current_page(1)
            self.show_window()
            return False
        if not result.text:
            self.refresh_history(sid)
            self.set_state('idle', 'No speech detected. Audio kept in History for retry or export.')
            self.notify('No speech detected', 'Your recording is saved in History.')
            return False
        self.emergency_audio.pop(sid, None)
        self.emergency_text.pop(sid, None)
        self.set_state('idle', 'Saved in History.')
        try:
            self.deliver(result.text)
        except Exception:
            self.paste_pending = False
            self.status.set_text('Paste did not complete. Your text and audio are saved in History.')
            self.notify('Text saved', 'Open History to copy your dictation.')
        GLib.timeout_add(100, self.refresh_after_paste, sid)
        return False

    def refresh_after_paste(self, sid):
        if self.quitting:
            return False
        if self.paste_pending:
            return True
        self.refresh_history(sid)
        return False

    def deliver(self, text):
        old = self.clipboard.wait_for_text() if self.settings["restore_clipboard"] else None
        self.clipboard.set_text(text, -1)
        self.clipboard.store()
        target = self.target
        focus = self.hotkey.focus() if self.hotkey else None
        if not self.settings["auto_paste"] or not target or focus != target or "blurt" in target[1]:
            self.status.set_text("Copied to clipboard. Paste when ready.")
            self.notify("Dictation copied", "Your text is on the clipboard.")
            return
        self.paste_pending = True
        began = time.monotonic()
        def paste():
            # Do not release physical modifiers on the user's behalf.
            pointer = self.hotkey.root.query_pointer()
            modifiers = X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask | X.Mod5Mask
            if pointer.mask & modifiers:
                if time.monotonic() - began < 2:
                    GLib.timeout_add(20, paste)
                    return False
                self.paste_pending = False
                self.notify("Dictation copied", "Release modifier keys, then paste when ready.")
                return False
            if self.hotkey.focus() != target:
                self.paste_pending = False
                self.notify("Dictation copied", "Focus changed; your text is on the clipboard.")
                return False
            terminal = any(s in target[1] for s in ("terminal", "xterm", "kitty", "alacritty", "konsole", "terminator"))
            try:
                subprocess.run(["xdotool", "key", "--", "ctrl+shift+v" if terminal else "ctrl+v"],
                               check=True, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if old is not None:
                    GLib.timeout_add(1000, restore)
                else:
                    self.paste_pending = False
            except (OSError, subprocess.SubprocessError):
                self.paste_pending = False
                self.notify("Paste unavailable", "Your dictation is still on the clipboard.")
            return False
        def restore():
            # Do not overwrite something copied by the user during the delay.
            if self.clipboard.wait_for_text() == text:
                self.clipboard.set_text(old, -1)
                self.clipboard.store()
            self.paste_pending = False
            return False
        GLib.idle_add(paste)

    def notify(self, title, body):
        note = Gio.Notification.new(title)
        note.set_body(body)
        self.send_notification("dictation", note)

    def selected_id(self):
        model, row = self.history_list.get_selection().get_selected()
        return model[row][0] if row is not None else None

    def refresh_history(self, prefer=None):
        if not self.store or not hasattr(self, 'history_model'):
            return
        selected = prefer or self.selected_id()
        try:
            rows = self.store.entries(self.show_trash.get_active())
        except (OSError, sqlite3.Error):
            self.status.set_text('Could not read recovery history. Saved audio files have not been removed.')
            return
        self.history_model.clear()
        statuses = {'recording':'Recording', 'transcribing':'Transcribing', 'saved':'Transcribed',
                    'paused':'Paused — retry available', 'interrupted':'Interrupted — retry available',
                    'failed':'Needs retry', 'no_speech':'No speech — retry available', 'imported':'Text recovered'}
        selected_iter = None
        self.history = []
        for row in rows:
            seconds = int(row['duration'])
            it = self.history_model.append([row['id'], time.strftime('%b %d · %H:%M', time.localtime(row['created'])),
                                            f'{seconds//60}:{seconds%60:02d}', statuses.get(row['status'], row['status'])])
            if row['id'] == selected:
                selected_iter = it
            if row['text']:
                self.history.append((time.strftime('%H:%M', time.localtime(row['created'])), row['text']))
        if selected_iter is None and len(self.history_model):
            selected_iter = self.history_model.get_iter_first()
        if selected_iter is not None:
            self.history_list.get_selection().select_iter(selected_iter)
        else:
            self.history_view.get_buffer().set_text('Your recordings and transcripts will appear here.\n\nCompleted audio: 48 hours\nUnfinished audio: 7 days\nTranscripts: 7 days')
        self.delete_button.set_sensitive(self.show_trash.get_active())

    def history_selected(self, *_):
        sid = self.selected_id()
        if not sid or not self.store:
            return
        try:
            row = self.store.get(sid)
            versions = self.store.versions(sid)
            audio_expiry, text_expiry = self.store.expiry(row)
            fmt = lambda t: time.strftime('%b %d at %H:%M', time.localtime(t))
            present = self.store.path(sid).exists() or sid in self.emergency_audio
            heading = ('Audio available until ' + fmt(audio_expiry)) if present else 'Audio unavailable or expired.'
            heading += '\nText/history expires ' + fmt(text_expiry)
            text = self.emergency_text.get(sid) or row['text']
            body = heading + '\n\n' + (text or row['error'] or 'Audio is saved. Use Retry to transcribe it.')
            if sid in self.emergency_audio or sid in self.emergency_text:
                body = 'STORAGE PROBLEM: this result has unsaved data in memory. Export/copy it before quitting.\n\n' + body
            if row['verbatim'] and row['verbatim'].strip() != row['text'].strip():
                body += '\n\nOriginal words\n' + row['verbatim']
            if len(versions) > 1:
                body += '\n\nEarlier transcription attempts\n' + '\n\n'.join(v['text'] for v in versions[1:])
            self.history_view.get_buffer().set_text(body)
        except (OSError, sqlite3.Error, ValueError):
            self.history_view.get_buffer().set_text('Could not read this recording. Your saved files have not been deleted.')

    def copy_selected(self, *_):
        sid = self.selected_id()
        if not sid:
            return
        row = self.store.get(sid)
        text = self.emergency_text.get(sid) or row['text']
        if not text:
            self.status.set_text('This recording has no transcript yet. Use Retry.')
            return
        self.clipboard.set_text(text, -1)
        self.clipboard.store()
        self.status.set_text('Transcript copied. It remains in History until its expiry date.')

    def copy_latest(self, *_):
        if self.store:
            rows = self.store.entries()
            for row in rows:
                text = self.emergency_text.get(row['id']) or row['text']
                if text:
                    self.clipboard.set_text(text, -1)
                    self.clipboard.store()
                    self.status.set_text('Latest dictation copied.')
                    return

    def retry(self, *_):
        sid = self.selected_id()
        if self.state != 'idle' or not sid:
            self.status.set_text('Select a recording and finish the current dictation first.')
            return
        if sid in self.pending_jobs:
            self.status.set_text('This recording is still being processed. Its result will be saved here.')
            return
        if not self.key:
            self.status.set_text('Add your AssemblyAI key in Settings before retrying.')
            return
        try:
            pcm = self.emergency_audio.get(sid) or self.store.audio(sid)
        except OSError:
            self.status.set_text('The audio for this recording has expired or is unavailable. Its saved text can still be copied.')
            return
        self.generation += 1
        self.target = None
        self.active_sid = sid
        self.set_state('transcribing', 'Retrying — previous text and audio stay saved.')
        self.run_job(sid, pcm=pcm)

    def export_audio(self, *_):
        sid = self.selected_id()
        if not sid:
            return
        if sid == self.active_sid or sid in self.pending_jobs:
            self.status.set_text('Finish this recording before exporting it.')
            return
        dialog = Gtk.FileChooserDialog(title='Export recording', transient_for=self.window,
                                        action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Save audio', Gtk.ResponseType.OK)
        dialog.set_current_name('dictation-' + time.strftime('%Y%m%d-%H%M%S') + '.wav')
        dialog.set_do_overwrite_confirmation(True)
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                destination = dialog.get_filename()
                if sid in self.emergency_audio:
                    with wave.open(destination, 'wb') as out:
                        out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE)
                        out.writeframes(self.emergency_audio[sid])
                else:
                    self.store.export_wav(sid, destination)
                self.status.set_text('Audio exported as a WAV file.')
            except (OSError, ValueError):
                self.status.set_text('Could not export the audio. Try another destination; the source was kept.')
        dialog.destroy()

    def trash_selected(self, *_):
        sid = self.selected_id()
        if not sid or sid == self.active_sid or sid in self.pending_jobs:
            self.status.set_text('Finish processing this recording before moving it.')
            return
        if sid in self.emergency_audio or sid in self.emergency_text:
            self.status.set_text('Export or copy this unsaved result before moving it.')
            return
        try:
            self.store.trash(sid, not self.show_trash.get_active())
            self.refresh_history()
            self.status.set_text('Recording moved. Trash can be restored until the original expiry date.')
        except (OSError, sqlite3.Error):
            self.status.set_text('Could not move this recording. It was kept.')

    def delete_selected(self, *_):
        sid = self.selected_id()
        if not sid or sid == self.active_sid or sid in self.pending_jobs:
            return
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.NONE, text='Permanently delete this recording?')
        dialog.format_secondary_text('This deletes its audio and all transcript versions. It cannot be restored from Blurt.')
        dialog.add_button('Keep recording', Gtk.ResponseType.CANCEL)
        dialog.add_button('Delete permanently', Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            try:
                self.store.delete_permanently(sid)
                self.refresh_history()
            except (OSError, sqlite3.Error, ValueError):
                self.status.set_text('Deletion did not complete. Check History and the recovery folder.')

    def check_microphone(self, *_):
        if self.state != 'idle':
            self.mic_info.set_text('Finish dictating before checking the microphone.')
            return
        if self.mic_test:
            self.end_mic_test()
            return
        try:
            self.mic_test = Recorder(self.mic_combo.get_active_id() or '')
            self.mic_test.start()
            self.mic_test_until = time.monotonic() + 10
            self.mic_info.set_text('Speak for a few seconds. This check is not saved or sent anywhere.')
        except OSError:
            self.mic_test = None
            self.mic_info.set_text('Could not open the microphone.')

    def end_mic_test(self):
        if self.mic_test:
            recorder, self.mic_test = self.mic_test, None
            try:
                recorder.stop()
                self.mic_info.set_text('Check finished. Quiet = low input; near clipping = input may distort. This does not assess speech-recognition quality.')
            except DictationError as exc:
                self.mic_info.set_text(str(exc))

    def cleanup_history(self):
        if self.store and self.state == 'idle':
            try:
                protected = set(self.pending_jobs) | set(self.emergency_audio) | set(self.emergency_text)
                self.store.cleanup(protected=protected)
                self.refresh_history()
            except (OSError, sqlite3.Error):
                self.status.set_text('History cleanup could not finish. Recovery files were left in place where possible.')
        return not self.quitting

    def request_quit(self, *_):
        if self.emergency_audio or self.emergency_text:
            self.show_window()
            self.book.set_current_page(1)
            dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
                message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE,
                text='Some dictation data could not be saved.')
            dialog.format_secondary_text('Export the audio or copy the text from History before quitting. Quitting now will lose data held only in memory.')
            dialog.add_button('Keep Blurt open', Gtk.ResponseType.CANCEL)
            dialog.add_button('Quit anyway', Gtk.ResponseType.ACCEPT)
            dialog.set_default_response(Gtk.ResponseType.CANCEL)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.ACCEPT:
                return
        self.quit()

    def shutdown_app(self, *_):
        self.quitting = True
        self.generation += 1
        self.end_mic_test()
        if self.recorder:
            if self.recorder.live:
                self.recorder.live.cancel()
            try:
                self.recorder.stop()
            except Exception:
                pass
            if self.active_sid:
                self.safe_update(self.active_sid, 'interrupted', 'App closed while recording. Saved audio is available to retry.')
        if self.hotkey:
            self.hotkey.close()
        if self.window:
            self.window.destroy()
            self.overlay.destroy()


if __name__ == '__main__':
    os.umask(0o077)
    if os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        raise SystemExit('Blurt Linux requires an X11 session.')
    app = Blurt()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (app.quit(), False)[1])
    app.run(None)

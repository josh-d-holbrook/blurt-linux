"""Small, dependency-free AssemblyAI dictation client and microphone capture."""
from __future__ import annotations

import array
import json
import math
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

ENDPOINT = "https://dictation.assemblyai.com/v1/transcribe/live"
RATE = 16000
MAX_SECONDS = 119
MAX_BYTES = RATE * 2 * MAX_SECONDS


def choose_brio(sources):
    for source in sources:
        if not source.get("name", "").endswith(".monitor") and "brio" in (
            source.get("name", "") + " " + source.get("description", "") + " " +
            json.dumps(source.get("properties", {}))
        ).lower():
            return source["name"]
    return ""


class DictationError(Exception):
    pass


def make_config(settings):
    config = {"sample_rate": RATE, "channels": 1}
    terms, seen = [], set()
    for term in settings.get("keyterms", "").split(","):
        term = term.strip()
        if term and term.casefold() not in seen:
            seen.add(term.casefold())
            terms.append(term)
    if len(terms) > 100 or sum(len(t.encode()) for t in terms) > 2048:
        raise DictationError("Use at most 100 key terms and 2048 UTF-8 bytes in total.")
    if terms:
        config["keyterms_prompt"] = terms
    instruction = settings.get("instruction", "").strip()
    if len(instruction.encode()) > 2048:
        raise DictationError("Style instructions must fit within 2048 UTF-8 bytes.")
    if instruction:
        config["llm_instruction"] = instruction
    language = settings.get("language", "en")
    config["language_codes"] = [language]
    return config


def multipart(pcm, config, boundary):
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="config"\r\n'
        "Content-Type: application/json\r\n\r\n"
        + json.dumps(config) + f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="audio"; filename="audio.pcm"\r\n'
        "Content-Type: audio/pcm\r\n\r\n"
    ).encode()
    return head + pcm + f"\r\n--{boundary}--\r\n".encode()


@dataclass
class Transcript:
    text: str
    verbatim: str
    rewrite_failed: bool


def parse_response(data, polished=True):
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise DictationError("AssemblyAI returned an invalid transcript response.")
    raw = data["text"]
    clean = data.get("llm_response")
    usable = isinstance(clean, str) and bool(clean.strip())
    return Transcript(clean.strip() if polished and usable else raw.strip(), raw,
                      bool(polished and not usable and raw.strip()))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Never forward a private API key to a redirected host.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transcribe(pcm, key, settings, opener=None):
    if not key or not key.strip():
        raise DictationError("Add your AssemblyAI API key in Settings first.")
    if len(pcm) < RATE * 2 * 0.15:
        raise DictationError("That recording was too short. Try speaking for longer.")
    if len(pcm) > MAX_BYTES or len(pcm) % 2:
        raise DictationError("Invalid recording length.")
    boundary = "blurt-linux-" + uuid.uuid4().hex
    body = multipart(pcm, make_config(settings), boundary)
    return send_request(body, boundary, key, settings, opener)


def send_request(body, boundary, key, settings, opener=None):
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        "Authorization": key.strip(),
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "BlurtLinux/1.0 (Linux)",
    })
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=90) as response:
            data = json.loads(response.read(2 * 1024 * 1024))
        return parse_response(data, settings.get("polished", True))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(8192))
            detail = detail.get("error") or detail.get("detail") or "Request rejected"
        except (ValueError, AttributeError):
            detail = "Request rejected"
        if exc.code in (401, 403, 404):
            detail = "Check your AssemblyAI API key and dictation API access."
        elif exc.code == 429:
            detail = "AssemblyAI rate or account limit reached. Try again later."
        # Avoid echoing credentials even if an upstream error reflects them.
        raise DictationError(f"AssemblyAI HTTP {exc.code}: {str(detail).replace(key, '[redacted]')[:400]}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DictationError("Could not reach AssemblyAI. Check your connection and retry.") from None
    except (ValueError, UnicodeError):
        raise DictationError("AssemblyAI returned an unreadable response.") from None


class StreamCancelled(Exception):
    pass


class LiveTranscriber:
    """Upload during speech; all microphone frames also go to local recovery."""
    def __init__(self, key, settings, opener=None):
        self.key = key
        self.settings = settings.copy()
        self.config = make_config(settings)
        self.opener = opener
        self.frames = queue.Queue()
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.bytes = 0
        self.started = False
        self.ended = False
        self.result = None
        self.error = None

    def write(self, chunk):
        if self.cancelled.is_set() or self.done.is_set():
            return
        self.bytes += len(chunk)
        if self.bytes > MAX_BYTES:
            raise DictationError('Recording exceeds the dictation limit.')
        self.frames.put(chunk)
        # Don't open a billed request for an accidental, very short key chord.
        if not self.started and self.bytes >= RATE * 2 * .20:
            self.started = True
            threading.Thread(target=self._run, daemon=True).start()

    def body(self, boundary):
        closing = f'\r\n--{boundary}--\r\n'.encode()
        head = multipart(b'', self.config, boundary)[:-len(closing)]
        if self.cancelled.is_set():
            raise StreamCancelled()
        yield head
        while True:
            chunk = self.frames.get()
            if self.cancelled.is_set():
                raise StreamCancelled()
            if chunk is None:
                break
            yield chunk
        yield closing

    def _run(self):
        try:
            boundary = 'blurt-linux-' + uuid.uuid4().hex
            self.result = send_request(self.body(boundary), boundary, self.key, self.settings, self.opener)
        except Exception as exc:
            self.error = exc
        finally:
            self.done.set()

    def end_audio(self):
        if not self.ended:
            self.ended = True
            self.frames.put(None)

    def finish(self):
        self.end_audio()
        if not self.started:
            raise DictationError('That recording was too short. Its audio is saved in History.')
        if not self.done.wait(95):
            self.cancel()
            raise DictationError('AssemblyAI timed out. Your audio is saved; retry from History.')
        if self.error:
            raise self.error
        return self.result

    def cancel(self):
        self.cancelled.set()
        self.frames.put(None)


class Recorder:
    """Capture on demand, with an optional durable recovery journal."""
    def __init__(self, source="", on_limit=lambda: None, journal=None, live=None):
        self.source = source
        self.on_limit = on_limit
        self.data = bytearray()
        self.level = 0.0
        self.error = None
        self.stopping = threading.Event()
        self.process = None
        self.thread = None
        self.journal = journal
        self.live = live
        self.rms_db = -120.0
        self.peak = 0.0

    def start(self):
        if self.source == "prefer-brio":
            try:
                sources = json.loads(subprocess.check_output(
                    ["pactl", "--format=json", "list", "sources"], timeout=3))
                self.source = choose_brio(sources)
            except (OSError, ValueError, subprocess.SubprocessError):
                self.source = ""
        cmd = ["parec", "--raw", "--format=s16le", "--rate=16000", "--channels=1",
               "--latency-msec=40", "--client-name=Blurt Linux"]
        if self.source:
            cmd.append("--device=" + self.source)
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while not self.stopping.is_set():
                chunk = self.process.stdout.read(1280)
                if not chunk:
                    if not self.stopping.is_set():
                        self.error = "Microphone stopped. Check the input device in Settings."
                    break
                chunk = chunk[:MAX_BYTES - len(self.data)]
                self.data.extend(chunk)
                if self.journal:
                    try:
                        self.journal.write(chunk)
                    except OSError:
                        self.error = "Recovery storage failed. This recording is still in memory; export it before quitting."
                        break
                if self.live:
                    self.live.write(chunk)
                samples = array.array("h", chunk)
                if samples:
                    rms = math.sqrt(sum(s*s for s in samples) / len(samples)) / 32768
                    self.level = min(1.0, rms * 6)
                    self.rms_db = 20 * math.log10(max(rms, .000001))
                    self.peak = max(abs(s) for s in samples) / 32768
                if len(self.data) >= MAX_BYTES:
                    self.on_limit()
                    break
        except (OSError, ValueError):
            self.error = "Could not read the microphone."
        finally:
            if self.process.poll() is None:
                self.process.terminate()

    def stop(self):
        self.stopping.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        if self.thread:
            self.thread.join(timeout=3)
        if self.process:
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()
            self.process.stderr.close()
        if self.live and not self.error:
            # Close the upload before recovery fsync/database work.
            self.live.end_audio()
        if self.journal:
            try:
                self.journal.close()
            except OSError:
                self.error = "Recovery storage could not be synced. Export this recording before quitting."
        if self.error:
            if self.live:
                self.live.cancel()
            raise DictationError(self.error)
        return bytes(self.data)


class KeyGesture:
    """Repeat-proof tap/hold decisions, independent of the desktop toolkit."""
    def __init__(self):
        self.down_at = None
        self.started = False

    def press(self, now, state):
        if self.down_at is not None:
            return None
        self.down_at = now
        self.started = state == "idle"
        return "start" if self.started else "stop" if state == "recording" else None

    def release(self, now, state):
        stop = (self.down_at is not None and self.started and
                now - self.down_at >= 0.35 and state == "recording")
        self.down_at = None
        self.started = False
        return "stop" if stop else None


class ModifierGesture:
    """A lone modifier dictates; modifier chords keep their normal meaning."""
    def __init__(self):
        self.down_at = None
        self.origin = None
        self.blocked = False

    def update(self, down, other_down, now, state):
        if down:
            if self.down_at is None:
                self.down_at = now
                self.origin = state
                self.blocked = other_down or state not in ("idle", "recording")
                if not self.blocked and state == "idle":
                    return "start"
            elif other_down and not self.blocked:
                self.blocked = True
                if self.origin == "idle" and state == "recording":
                    return "cancel"
            return None
        if self.down_at is None:
            return None
        stop = (not self.blocked and state == "recording" and
                (self.origin == "recording" or now - self.down_at >= 0.35))
        self.down_at = None
        self.origin = None
        self.blocked = False
        return "stop" if stop else None

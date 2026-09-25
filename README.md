# Blurt for Linux

Fast, system-wide voice dictation for Linux, using the **AssemblyAI Dictation API**.
An independent community adaptation of [AssemblyAI's Blurt for macOS](https://github.com/AssemblyAI/blurt),
with local recovery for interrupted or failed dictations.

**Tested on Debian 13, XFCE, and X11. Wayland is not supported.** Other X11 desktops
may work, but have not been verified. This is an early community release.

| Recording | Final 20 seconds |
| :---: | :---: |
| ![Recording orb and voice bars](assets/overlay-preview.png) | ![Time remaining before the limit](assets/countdown-preview.png) |

## What it does

- Tap a global shortcut to start/stop, or hold it while speaking.
- Uploads audio **while you speak**, following Blurt's streaming design.
- Pastes polished text into the original app, or copies it if focus changed.
- Saves audio during recording and saves the transcript **before paste**.
- Keeps a short recovery history with Retry, Copy, WAV export, and reversible Trash.
- Shows a small recording pill, with a countdown during the final 20 seconds.
- Supports microphone selection, language, key terms, and rewrite instructions.

No local speech model or Electron runtime is required. Transcription needs an
internet connection, your own AssemblyAI API key, and API usage is billed to your
AssemblyAI account. This project is not an official AssemblyAI Linux release.

## Install on Debian 13

1. Download this repository using **Code → Download ZIP**, then extract it.
   Alternatively, clone it with Git.
2. Open a terminal in the extracted project folder and install the dependencies:

   ```sh
   sudo apt update
   sudo apt install python3 python3-gi python3-cairo python3-gi-cairo python3-xlib \
     gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-secret-1 \
     pulseaudio-utils xdotool gnome-keyring
   ```

3. Install the app **as your normal user**, without sudo:

   ```sh
   sh install.sh
   ```

4. Open **Blurt Linux** from your application menu. Add your
   [AssemblyAI API key](https://www.assemblyai.com/dashboard/api-keys), select a
   microphone and shortcut, and click **Save settings**.

The installer adds a menu entry and starts Blurt automatically at future logins.
It installs into `~/.local/share/blurt-linux/` and backs up existing Python files
when run again. To try it without installation: `/usr/bin/python3 blurt.py`.

## Use

Focus a text field, tap your shortcut, speak, then tap again. Alternatively, hold
for more than 0.35 seconds and release to finish. Recording stops automatically at
119 seconds, just below the API's two-minute limit.

The default shortcut is **Right Super**: usually the right Windows key on a PC
keyboard or Right Command on a Mac-layout keyboard. Right Alt/Option, Right
Control, and several function keys are available in Settings. Some keyboards
require **Fn** to send an actual function key instead of a media command.

The default microphone setting prefers a connected Logitech Brio and otherwise
uses the system default. You can choose a fixed device or the system default
instead. **Check microphone** tests input locally without an API request.

- **Esc during recording:** pause and retain audio for Retry in History.
- **Esc during transcription:** suppress paste; the response is still saved.
- **Closing the settings window:** keep running in the tray. Use the tray's Quit
  item to exit.
- **Modifier combinations:** ordinary keyboard shortcuts pass through. A combination
  cancels a recording started by that same modifier press.

## Recovery and privacy

| Data | Kept locally for |
|---|---|
| Successfully transcribed audio | 48 hours after transcription |
| Failed, paused, interrupted, or no-speech audio | 7 days after recording |
| Transcripts and earlier successful attempts | 7 days after the latest success |

Cleanup runs at startup and hourly while idle. Starting another recording does
not replace an earlier one. Retry sends another API request and can incur another
charge. Exported files are not automatically deleted.

Recovery is designed for accidental cancellation, app crashes, failed requests,
and paste failures. It cannot guarantee survival of disk failure or sudden power
loss. Local audio/text files are private to your user but are not separately
encrypted by the app.

Audio is sent over HTTPS to AssemblyAI during recording. Screen contents,
clipboard contents, and text from other apps are not sent as context. Your API key
is stored in the desktop keyring. Local retention does not determine AssemblyAI's
server-side retention.

See [recovery and privacy details](docs/recovery-and-privacy.md), including file
locations, durability limits, and clipboard behavior.

## Troubleshooting

- **Wayland session:** this implementation requires X11/Xorg. Check with
  `echo "$XDG_SESSION_TYPE"`.
- **Nothing happens on the shortcut:** select another key in Settings. A shortcut
  may be reserved by your desktop, or your keyboard may require Fn.
- **No audio:** use Check microphone and choose the correct input. PulseAudio, or
  a PipeWire setup providing PulseAudio compatibility, must expose the input to
  `pactl` and `parec`. PipeWire configurations have not been independently tested.
- **Keyring problem:** a working Secret Service provider must be available and
  unlocked. `gnome-keyring` is one option. `ASSEMBLYAI_API_KEY` is also supported
  as an environment override; never commit your key or put it in an issue.
- **Text wasn't pasted:** open History and copy it. Paste checks the original
  window, not an individual field; unusual apps may need clipboard-only mode.
  Clipboard restoration covers text, not images or rich content.
- **No tray icon:** your desktop needs an AppIndicator-compatible tray.

Bug reports should include the distribution, desktop, X11/Wayland session type,
and reproduction steps. Please remove API keys, transcripts, and private audio.

## Development

```sh
/usr/bin/python3 -m unittest discover -v
/usr/bin/python3 -m py_compile blurt.py core.py recovery.py appearance.py
sh -n install.sh
```

Automated tests use synthetic audio and simulated requests: they need no API key,
real microphone, or network access. See [validation](docs/validation.md) and
[contributing](CONTRIBUTING.md).

## Uninstall

Quit from the tray, then remove these application files:

- `~/.local/share/blurt-linux/`
- `~/.local/share/applications/blurt-linux.desktop`
- `~/.config/autostart/blurt-linux.desktop`

Settings and recovery data remain unless you also remove
`~/.config/blurt-linux/` and `~/.local/state/blurt-linux/`. Removing the state
folder permanently deletes local recovery audio and transcripts. The keyring
entry can be removed in Passwords and Keys. These paths follow their corresponding
XDG environment variables if you have customized them.

## Credits and license

Original Blurt was created by **Alex Kroman at AssemblyAI**. This project follows
its streaming request design and recreates its compact overlay for GTK/X11.
The original reference revision is
[`158c6dc`](https://github.com/AssemblyAI/blurt/tree/158c6dc432fa9d997d5ff0ac6a2c71ad082ef8d8).

The Linux adaptation was built with AI coding assistance and tested through
personal use on Debian. Durable recovery and the near-limit countdown are
intentional additions. Published Blurt behavior is the baseline; differences
should have a concrete reason, such as Linux integration or protecting dictations.

[MIT license](LICENSE), retaining the original copyright notice.

- [AssemblyAI Dictation documentation](https://www.assemblyai.com/docs/dictation)
- [API reference](https://www.assemblyai.com/docs/api-reference/dictation-api/transcribe-live)

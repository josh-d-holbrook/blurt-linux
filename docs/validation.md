# Validation

Last checked for the initial public package: September 24, 2026.

## Automated tests

The suite covers API framing and errors, streamed audio delivery before stop,
polished/verbatim response fallback, recording limits, shortcut gestures,
recovery after abrupt process exit, independent audio/text retention, protected
active recordings, retry history, reversible Trash, permissions, WAV export,
paste/modifier/focus handling, overlay rendering, and the countdown boundary.

Tests use temporary recovery directories, synthetic PCM, and simulated API
responses. They do not use a real API key or send microphone audio to AssemblyAI.

## Desktop checks

During development, checks on Debian 13/XFCE/X11 with PulseAudio included real
Brio microphone capture, shortcut handling, GTK text insertion, clipboard
restoration, failed-request retry, cancellation, saved text before paste, and
recovery after restarting. Overlay appearance, placement, reduced motion,
rapid reopening, and stopping animation while hidden were also checked.

The app has been used for actual speech dictation on that setup. The streaming
transport also passed a real endpoint check using generated silence. Silence is
not a latency benchmark, and there has been no controlled Mac-versus-Linux speed
comparison. Other distributions, desktops, and Wayland have not been validated.

Tests simulate application failures; they do not prove survival of power loss,
disk failure, or a full disk in every possible condition. Read the documented
recovery limits before relying on the application.

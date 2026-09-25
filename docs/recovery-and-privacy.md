# Recovery and privacy

## Recover a dictation

Audio is written to a private local recovery file as it arrives. A completed
transcript is committed to the local database **before** automatic paste.

- **Esc while recording:** stop and keep the audio. Open History and choose Retry.
- **Esc while transcribing:** suppress paste; the eventual response still goes to History.
- **Network/API failure:** audio stays available for Retry or Export audio (WAV).
- **Paste fails or goes to the wrong place:** copy the saved text from History.
- **App closes or crashes:** reopen it; interrupted recordings are marked for retry.
- **Retry:** earlier successful text versions remain available in the history detail.
  It sends another API request and may incur another charge.
- **Trash:** reversible. Permanent deletion is a separate, confirmed action.

Starting a new recording does not replace a previous recording. Copying or pasting
text does not delete its recovery entry. History shows individual expiry dates.

| Data | Automatic retention |
|---|---|
| Audio with a successful transcript | 48 hours after successful transcription |
| Failed, paused, interrupted, or no-speech audio | 7 days after recording |
| Transcripts, original words, and earlier successful attempts | 7 days after latest success |
| Other history entries, including failed attempts | 7 days after recording |

Trash uses the same expiry rules. Cleanup runs at startup and hourly while idle;
if the app is closed or busy, expired files wait for its next cleanup. Active
recordings and requests are protected. Exported copies are yours to manage.

A full 119-second clip occupies about **3.81 MB** (16 kHz mono, 16-bit PCM).
One hundred full clips occupy about 381 MB; transcript text is much smaller.
This is a short recovery window, not an archive or a fixed total-storage quota.

Recovery protects against ordinary app crashes, accidental cancellation, failed
requests, and unsuccessful paste. It cannot promise zero loss under every condition.
Audio is written without application buffering and synced about every 250 ms and
at stop. Sudden power loss may lose the newest unsynced audio and capture buffers;
disk failure, a full disk, or explicit deletion can also defeat recovery. Storage
errors are surfaced, and unsaved data is held in memory for export/copy where
possible. Ordinary Quit warns if known unsaved data remains in memory.

## Data and privacy

Local audio and text live in `~/.local/state/blurt-linux/` (or `$XDG_STATE_HOME`).
The directory is private to your user; files are not separately encrypted by Blurt.
Deletion is ordinary filesystem/database deletion, not guaranteed forensic erasure.
AssemblyAI receives audio over HTTPS during dictation and on retry. Its server-side
retention is separate from this local policy; this app does not promise that
AssemblyAI immediately deletes it. No screen, clipboard, or other-app text is sent
as context. Language, key terms, and optional style instructions accompany audio.

Your API key stays in the desktop Secret Service keyring, outside recovery history.
`ASSEMBLYAI_API_KEY` can override it. Non-secret settings are in
`~/.config/blurt-linux/settings.json`.

Automatic paste checks the original window, not the individual text field. Keep the
intended field focused. When focus changes, text is copied instead. Terminal paste
uses Ctrl+Shift+V; ordinary paste uses Ctrl+V. Clipboard restoration covers text only
and does not overwrite a newer copy. Settings supports clipboard-only operation.

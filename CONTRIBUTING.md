# Contributing

Issues and pull requests are welcome. This is an early community project, tested
primarily on Debian 13/XFCE/X11; support for every desktop is not promised.

Please describe the problem, expected behavior, distribution, desktop environment,
and session type. Use synthetic examples rather than private dictations. Never
attach API keys, a recovery database, or personal audio to a public issue.

Before sending a change, run the checks in the README. The automated suite does not
need a key, microphone, or live API connection. Changes to shortcuts, clipboard
insertion, or rendering may additionally need a focused desktop check.

Preserve these design constraints:

- Use the AssemblyAI Dictation API and upload during speech. Avoid adding delays
  between finishing a recording and delivering text.
- Keep published Blurt behavior as the baseline. Explain material departures.
- Preserve audio during capture and commit transcripts before paste. Failed
  requests, cancellation, retries, and new recordings must not overwrite recovery.
- Keep local recovery temporary and private. Never log keys, audio, or transcripts.
- Scope animation work to the visible overlay and keep it inexpensive.

Small, focused changes are easier to review. Explain what changed and how it was
validated. Include the original MIT attribution when reusing upstream work.

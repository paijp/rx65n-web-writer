# Notes for Claude

This software is not official Renesas Electronics software. Keep the notice
(README.md, NOTICE, the page footer, `rxflash.py --help`) wherever it is, and
say no more about how the probe's protocol was obtained than "independent
investigation".

## Browser page (web/)

When you drive the board through the relay (`rx65nctl` on a server), the
user often has the page in the foreground on an Android phone and cannot see
Claude. So, every time:

- **Whenever you stop and the user should look at Claude** (a result, a
  question, an error), run `rx65nctl wait "<one-line summary>"` before
  ending your turn: yellow banner + two short vibrations.
- **When the work is finished**, run `rx65nctl close "<one-line summary>"`:
  green banner + one long vibration; the session then ends.
- Vibration works only after the user has tapped the page once; ask for a
  tap in the first message. `rx65nctl events` shows a `vibrate` event with
  `ok` / `activated` for each attempt.
- After giving the user a session URL, end the turn (text is shown only
  when the turn ends); a reconnect does not need a new session.

After changing `index.html` or `app.js`, run `python3 web/make-offline.py`
and `node tests/run.mjs`, and commit `web/rx65n-web-writer.html` with them.

See web/README.md for the rest.

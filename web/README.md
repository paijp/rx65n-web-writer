# web/ - write and watch an RX65N board from a browser tab

`app.js` talks to the board's on-board E2 Lite (`045b:82a0`) with **WebUSB**:
it puts the MCU in serial boot mode, erases, writes and verifies a `.mot`,
then resets it and reads its serial port (SCI1) through the probe's UART
pass-through. Chrome / Edge only (Firefox and Safari have no WebUSB), served
over HTTPS or `http://localhost`. Tested on Android; see the top README.

This is not official Renesas Electronics software and is not certified,
warranted or supported by Renesas Electronics.

## Standalone

Serve `index.html` and `app.js` from any static host (the Pages workflow does
this, with `samples/`), or `python3 -m http.server` and open
`http://localhost:8000/`. If `samples/index.json` exists next to the page (a
list of `{"file": "...", "title": "..."}`), the page offers those files.

`rx65n-web-writer.html` is `index.html` with `app.js` inlined, to open from
disk. Regenerate it with `python3 make-offline.py` after changing either.
The two-file form exists because the relay serves the page with a Content
Security Policy of `script-src 'self'` (a script that got into the page could
not run), which needs the script in its own file.

What the page refuses:

- anything aimed at option-setting memory (records there that are all FF,
  the unprogrammed value, are dropped; any other value stops the write);
- addresses outside the memory areas the MCU reports;
- an S-record file with a bad checksum.

## Relay mode (driven from a server, e.g. by Claude)

Opened as `https://<host>/rx65n/#t=<token>`, the page hides the manual
controls (only the connect button, messages and log remain), polls a small
relay for jobs and uploads the log and what it did, so someone with shell
access to the server - for example Claude through an `exec_command`
connector - can write and run programs on the board on the user's desk while
the user only keeps the tab open.

| File | Runs where | What it is |
|---|---|---|
| `index.html`, `app.js` | browser | the page (public; works without a token) |
| `server.py` | server, `python:3.13-slim` in podman, stdlib only | relay: sessions, jobs, log/event upload, file download |
| `rx65nctl` | server, as root | mint sessions, post jobs, read logs |
| `nginx.conf` | server nginx | the `location /rx65n/` block |

Setup: put `index.html`, `app.js` and `server.py` in `/srv/rx65n/app/`, and
`rx65nctl` in `/srv/rx65n/` (mode 700); add the nginx block; set `RXW_URL`
(e.g. `https://example.com/rx65n/`) for `rx65nctl`. `RXW_DATA` (`/srv/rx65n`),
`RXW_PORT` (5310) and `RXW_PREFIX` (`/rx65n`) can be changed the same way.
`rx65nctl new` starts the container when needed; the server exits a minute
after the last session expires. With `RXW_SERVER=external`, rx65nctl starts
nothing and expects `server.py` to be run some other way.

```
rx65nctl new [minutes]                 mint a session (default 60), start the container, print URL
rx65nctl msg <text>                    progress message on the page
rx65nctl wait [text]                   highlighted "look here" message (yellow, two short vibrations)
rx65nctl close [text]                  end the session (green, one long vibration)
rx65nctl run [sec] [bps]               reset and run what is in flash, log sec seconds (default 10, 115200)
rx65nctl flash <file.mot> [sec] [bps]  write, verify, run, log
rx65nctl hold                          hold the board in reset
rx65nctl jobwait [sec]                 block until the current job ends (default 50)
rx65nctl log [bytes]                   tail of the session's log (log.bin)
rx65nctl events [n]                    last n events the page reported (JSON lines)
rx65nctl status                        control.json, whether the page was opened, time left
rx65nctl askreload [text]              ask the user to reload by hand
rx65nctl reload                        make open pages reload (after editing app.js)
(run and flash take --tx TEXT, escapes like \r\n allowed: sent 1 s after the program starts)
```

The page uploads everything received while connected, not only during jobs;
each run starts with a `=== job jN <kind> <time> ===` marker and a send is
marked `=== tx N bytes ===`.

Typical loop:

```
cd /srv/rx65n && ./rx65nctl flash img/foo.mot 8 --tx 'hello\r\n' && ./rx65nctl jobwait 45
./rx65nctl log 2000
```

Rates: the probe's UART is 3 MHz / n, so 9600, 115200 (as 3e6/26 = 115385),
125000 and 1000000 are accepted; at 115200 the MCU needs its PLL.

### Session flow

**Rule for Claude:** the user may be watching only this page (e.g. Android
with the browser in the foreground). Every time Claude stops for the user, it
runs `rx65nctl wait "<summary>"` first, and `rx65nctl close "<summary>"` when
done; `rx65nctl new` and `jobwait` print a reminder. See ../CLAUDE.md.

1. `rx65nctl new 30` prints the URL. When Claude hands it out, it should end
   its turn right after showing it (the Claude app shows a turn's text only
   when the turn ends) and come back via a scheduled check-in (the
   Claude_Code_Remote `send_later` tool, which outside Auto mode needs the
   user's approval - so schedule it before minting the URL) or the user's
   next message, then wait for a `claimed` event in `rx65nctl events`.
2. The user opens the link in Chrome and, the first time only, connects.
3. Jobs, `jobwait`, `log`, repeat.
4. `rx65nctl wait "..."` when the user should look at Claude; `rx65nctl
   close` at the end. On phones vibration needs one tap on the page first
   (Chrome's user-activation rule); each attempt is reported as a `vibrate`
   event with `ok` and `activated`.

### Security

- The token is the only credential: 32 random characters minted by
  `rx65nctl` on the server. It travels in the URL fragment (never sent to the
  server, so not in logs or Referer), is moved to `sessionStorage` and removed
  from the address bar, and is sent as `X-Token`.
- Every `api/` request with a bad or expired token is a plain 404. Sessions
  expire (default 60 min); `close` cuts them to 10 min, read-only (events are
  still accepted, so the page can report its closing vibration).
- Sessions are stored under `sha256(token)`. The container is `--rm`.
- CSP: scripts from self only, connect to self only, no framing.

## Notes on the implementation

- WebUSB reads have no timeout and cannot be cancelled, so the page never
  reads "just in case". A reply whose header is not the command just sent is
  taken for a stale one from an earlier, cut-off session and skipped.
- All USB traffic goes through one queue (`withLink`): the serial monitor's
  polling, a write and the buttons never interleave. Anything typed rides on
  the next poll's 0x68.
- The write plan splits an area's data where the gap is wider than one erase
  block, so code at both ends of a large area does not erase and write the
  whole area.
- An exception that escapes a job's error path would leave the page busy for
  good; `flush()` is written so it cannot throw, and a reload request is
  honoured after 2 minutes of busy.
- The user agent string is not reliable (Android Chrome in desktop-site mode
  says `X11; Linux x86_64`); the `loaded` event also reports
  `navigator.userAgentData` platform, version and model.

## Tested

`node tests/run.mjs` (from the repository root, with playwright) runs the page
in headless Chromium against `tests/mock-e2.js`, a simulated probe and MCU:
standalone write and verify, the option-setting memory guard, sending text,
hold, and a relay session (flash / run / hold jobs, wait, close) through
`server.py` and `rx65nctl`. On real hardware: an RX65N Envision Kit with
Chrome on Android.

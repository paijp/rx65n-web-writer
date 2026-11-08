"""Read the MCU's serial output through the probe's UART pass-through.

Boot mode uses the same pass-through, so once user code is running and drives
TXD1 the bytes it sends come back through E2 command 0x68. Confirmed on the
Envision Kit: TXD1 is on P26 there.

How a block read behaves, established by probing the board:

* The reply is always as long as the read that was asked for.
* Status 0 means the whole requested length arrived, so every byte is real.
* Status 03 58 00 00 means the read timed out. The bytes that did arrive come
  first and the remainder is 0xFF filler - the data is not lost.
* Reads of at least 4096 bytes are accepted, and data that arrives while
  nothing is being read is buffered by the probe.

So a block read plus trimming the trailing filler recovers the output far more
cheaply than one USB round trip per byte. The trim is exact for text, which
never contains 0xFF. For binary output that may end in 0xFF bytes, use
`exact=True`, which reads a byte at a time and relies only on the status.
"""

import os
import struct
import sys
import time

from e2ob import PAD, STATUS_OK, STATUS_TIMEOUT, E2Error


def set_rate(link, bps):
    from rxboot import LINK_DIVISOR
    if bps not in LINK_DIVISOR:
        raise E2Error("log rate %d is not one of the rates known to work here: %s"
                      % (bps, ", ".join(str(k) for k in sorted(LINK_DIVISOR))))
    link.command(0x62, struct.pack(">HH", 0, LINK_DIVISOR[bps]))


def read_block(link, size, poll_ms):
    """Return (data, filled, status).

    `filled` is True when every requested byte arrived, which is the only case
    where the data needs no trimming. Any other status is reported to the
    caller rather than raised: a receive error on one block should not end a
    log, and the bytes that did arrive are still in front of the filler.
    """
    link.set_timeout(poll_ms)
    data, status = link.exchange(b"", size, timeout=poll_ms + 2000)
    if status == STATUS_OK:
        return data, True, status
    return data.rstrip(bytes((PAD,))), False, status


def drain(link, seconds=0.6, block=4096):
    """Throw away whatever the probe has buffered from before now.

    Worth doing before releasing reset: anything already in the buffer was
    produced by the previous program and would otherwise appear as the first
    lines of the new one's output.
    """
    started = time.time()
    while time.time() - started < seconds:
        data, filled, _ = read_block(link, block, 50)
        if not data and not filled:
            return


def read_exact(link, poll_ms):
    """One byte at a time: slower, but never has to guess where the data ends."""
    out = bytearray()
    while True:
        data, filled, _ = read_block(link, 1, poll_ms)
        if not filled:
            return bytes(out)
        out += data
        if len(out) >= 4096:
            return bytes(out)


def stream(link, out=None, bps=115200, block=256, poll_ms=100, exact=False,
           stop_after=None, on_data=None, on_status=None, set_rate_first=True):
    """Copy MCU output to `out` until interrupted. Returns the byte count.

    `on_status` is called with each status word that is neither a complete read
    nor a plain timeout, so the caller can report receive errors without the
    log stopping.
    """
    out = out or sys.stdout
    if set_rate_first:
        set_rate(link, bps)
    started = time.time()
    total = 0
    while True:
        if stop_after is not None and time.time() - started >= stop_after:
            break
        if exact:
            data, filled, status = read_exact(link, poll_ms), False, STATUS_TIMEOUT
        else:
            data, filled, status = read_block(link, block, poll_ms)
        if not filled and status != STATUS_TIMEOUT and on_status:
            on_status(status)
        if data:
            total += len(data)
            if on_data:
                on_data(data)
            else:
                out.write(data.decode("utf-8", "replace"))
                out.flush()
        # A filled read means there may be more waiting, so go straight back
        # for it; an unfilled one has already spent the poll interval.
    return total


def expect(link, prefix, bps=115200, timeout=5.0, poll_ms=50, send=None):
    """Read lines until one starts with `prefix`, and return it without the CRLF.

    For talking to firmware that answers in lines rather than streaming, which
    is what `rxflash.py ofs` needs: it has to read one specific reply and act on
    it, not print everything. Returns None if `timeout` passes first, so the
    caller can say what did not answer rather than hanging.

    `send` is transmitted once at the start. The same 0x68 command carries both
    directions, so that is the same call with bytes to write.
    """
    set_rate(link, bps)
    if send is not None:
        link.exchange(send, 0, timeout=poll_ms + 2000)
    prefixes = (prefix,) if isinstance(prefix, bytes) else tuple(prefix)
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data, _, _ = read_block(link, 256, poll_ms)
        if data:
            buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip(b"\r")
            for p in prefixes:
                if line.startswith(p):
                    return line
    return None


def ask(link, payload, prefix, bps=115200, timeout=5.0, poll_ms=50):
    """Send `payload` and return the first reply line starting with `prefix`."""
    return expect(link, prefix, bps=bps, timeout=timeout, poll_ms=poll_ms,
                  send=payload)


def interact(link, bps=115200, block=256, poll_ms=50, out=None, stdin=None,
             linger=2.0, send_after=0.5):
    """Two-way terminal: MCU output to `out`, `stdin` to the MCU.

    E2 command 0x68 carries both directions, so sending is the same call with
    bytes to transmit and nothing to read. Confirmed on the board: bytes sent
    this way arrive at the MCU and its echo comes back.

    When stdin is a terminal it is put into cbreak mode so keystrokes go
    through as they are typed; when it is a pipe or a file the data is sent as
    it is read. After end of input it keeps reading for `linger` seconds, so a
    piped command still sees the answer to what it sent.

    Nothing is sent for the first `send_after` seconds. Straight after a reset
    the MCU is still setting up its clock and its UART, and input sent into
    that window is received as garbage or not at all.
    """
    import select

    out = out or sys.stdout
    stdin = stdin or sys.stdin
    set_rate(link, bps)

    fd = stdin.fileno()
    is_tty = os.isatty(fd)
    saved = None
    if is_tty:
        import termios
        import tty
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    eof_at = None
    started = time.time()
    try:
        while True:
            if eof_at is not None and time.time() - eof_at >= linger:
                break
            if eof_at is None and time.time() - started >= send_after:
                ready, _, _ = select.select([fd], [], [], 0)
                if ready:
                    chunk = os.read(fd, 4096)
                    if chunk:
                        for i in range(0, len(chunk), 256):
                            link.set_timeout(1000)
                            link.exchange(chunk[i:i + 256], 0, timeout=3000)
                    elif not is_tty:
                        eof_at = time.time()
            data, _, _ = read_block(link, block, poll_ms)
            if data:
                out.write(data.decode("utf-8", "replace"))
                out.flush()
    finally:
        if saved is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

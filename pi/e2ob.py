"""E2 OB transport layer.

NOT official Renesas Electronics software, and not certified, warranted or
supported by Renesas Electronics. The probe's command set is from independent
investigation. The MCU-side protocol implemented on top of this is the documented
RX65N boot-mode serial protocol; this module only carries those bytes over
the board's USB debug interface.
"""

import struct
import sys

import usb.core
import usb.util

VID = 0x045B
PID = 0x82A0
EP_OUT = 0x02
EP_IN = 0x81

# 0x68 status words seen so far. The reply is always as long as the read that
# was asked for: when fewer bytes arrived in time, the ones that did arrive
# come first and the rest is PAD.
STATUS_OK = b"\x00\x00\x00\x00"
STATUS_TIMEOUT = b"\x03\x58\x00\x00"
PAD = 0xFF


class E2Error(Exception):
    pass


class E2OB:
    """Bulk-endpoint command channel.

    Every packet starts with 0x28 followed by a command byte; replies echo the
    same two bytes.
    """

    def __init__(self, verbose=False):
        self.verbose = verbose
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise E2Error("E2 OB (%04x:%04x) not found" % (VID, PID))
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass

        # Only configure it if it is not configured already. Setting the
        # configuration on a device that has one resets its endpoints, which
        # disturbs anything else mid-transfer for no gain.
        #
        # Do NOT reach for dev.reset() when this fails. On the E2 OB that
        # leaves the probe answering nothing - descriptor reads time out and
        # the kernel eventually disconnects it - and nothing short of
        # unplugging the USB cable brings it back. Twice. A stale claim from a
        # process that was killed is cleared by letting that process exit
        # properly instead: stop it with SIGTERM, not SIGKILL, so its close()
        # runs.
        try:
            configured = dev.get_active_configuration() is not None
        except usb.core.USBError:
            configured = False
        if not configured:
            dev.set_configuration()
        self.dev = dev
        self.flush()

    def flush(self):
        """Discard anything still queued on the IN endpoint.

        A session that was killed mid-transfer leaves its last reply unread,
        and that reply then turns up as the header of the next session's first
        one - which reads as a protocol desync ("unexpected reply header
        28 68 ...") on a link that is in fact fine. Reading until the endpoint
        is empty clears it, and is what to do instead of resetting the device.
        """
        dropped = 0
        while True:
            try:
                data = self.dev.read(EP_IN, 4096, 30)
            except usb.core.USBError:
                break
            if not data:
                break
            dropped += len(data)
            if dropped > 1 << 20:
                break
        if dropped and self.verbose:
            print("  usb: dropped %d stale bytes" % dropped, file=sys.stderr)
        return dropped

    def close(self):
        usb.util.dispose_resources(self.dev)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def command(self, cmd, payload=b"", timeout=10000, reply_max=4096):
        """Send one 0x28 command, return the reply body (after the echoed header).

        `reply_max` has to be at least as large as the reply, or the tail of it
        stays queued on the endpoint and shows up as the header of the next
        reply. A 4096-byte read, for instance, answers with 4102 bytes.
        """
        packet = bytes((0x28, cmd)) + bytes(payload)
        if self.verbose:
            print("  usb >> %s" % packet[:48].hex(" "), file=sys.stderr)
        self.dev.write(EP_OUT, packet, timeout)
        reply = bytes(self.dev.read(EP_IN, max(4096, reply_max), timeout))
        if self.verbose:
            print("  usb << %s" % reply[:48].hex(" "), file=sys.stderr)
        if reply[:2] != packet[:2]:
            raise E2Error("unexpected reply header %s (expected %s); the endpoint "
                          "is out of step, usually a reply that did not fit"
                          % (reply[:4].hex(" "), packet[:2].hex(" ")))
        return reply[2:]

    def set_timeout(self, ms):
        """0x6b: per-operation timeout used by the UART pass-through."""
        self.command(0x6B, struct.pack(">I", ms))

    def exchange(self, tx=b"", rx_len=0, timeout=10000):
        """0x68: write `tx` to the MCU UART, then read up to `rx_len` bytes.

        Wire format is
            28 68 <tx_len:be16> <rx_len:be16> <tx bytes>
        and the reply is
            28 68 <rx bytes> <status:be32>

        Returns (data, status) and leaves judging the status to the caller,
        because a read that did not fill is normal when tailing a log.
        """
        body = struct.pack(">HH", len(tx), rx_len) + bytes(tx)
        reply = self.command(0x68, body, timeout, reply_max=rx_len + 64)
        if len(reply) < 4:
            raise E2Error("short 0x68 reply %s" % reply.hex(" "))
        return reply[:-4], reply[-4:]

    def transfer(self, tx=b"", rx_len=0, timeout=10000):
        """As exchange(), but anything other than a complete read is an error."""
        data, status = self.exchange(tx, rx_len, timeout)
        if status != STATUS_OK:
            raise E2Error("0x68 status %s" % status.hex(" "))
        return data

    def read_more(self, n, timeout=10000):
        """0x68 with no transmit: pull `n` further bytes already buffered."""
        return self.transfer(b"", n, timeout)


# --- RX boot-mode framing (documented in the RX65N hardware manual) ---

SOH = 0x01  # command frame header
SOD = 0x81  # data frame header, in either direction
ETX = 0x03  # terminator of a command frame, or of the last data frame
ETB = 0x17  # terminator of a data frame that more data follows


def build(cmd, data=b"", head=SOH, last=True):
    """Frame a packet: <head> LNH LNL CMD <data> SUM <terminator>.

    Commands use SOH. Bulk data the host sends during a write uses SOD, and
    every such frame but the final one ends with ETB rather than ETX. The
    checksum covers the length, command and data, not the header byte.
    """
    body = struct.pack(">H", len(data) + 1) + bytes((cmd,)) + bytes(data)
    checksum = (-sum(body)) & 0xFF
    return bytes((head,)) + body + bytes((checksum, ETX if last else ETB))


def parse(frame, expect=None):
    """Validate an MCU response frame and return (cmd, data).

    Raises E2Error on a malformed frame. An error response (command byte with
    bit 7 set) is returned as-is; callers decide how to report it.
    """
    if len(frame) < 6:
        raise E2Error("frame too short: %s" % frame.hex(" "))
    if frame[0] != SOD:
        raise E2Error("bad SOD %02x" % frame[0])
    if frame[-1] not in (ETX, ETB):
        raise E2Error("bad terminator %02x" % frame[-1])
    length = struct.unpack(">H", frame[1:3])[0]
    if len(frame) != length + 5:
        raise E2Error("length %d does not match frame of %d bytes" % (length, len(frame)))
    if sum(frame[1:-1]) & 0xFF:
        raise E2Error("checksum mismatch: %s" % frame[:16].hex(" "))
    cmd, data = frame[3], frame[4:-2]
    if expect is not None and cmd not in (expect, expect | 0x80):
        raise E2Error("expected reply to %02x, got %02x" % (expect, cmd))
    return cmd, data

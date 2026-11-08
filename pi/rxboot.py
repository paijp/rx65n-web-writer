"""RX65N boot-mode session over the E2 OB pass-through.

The command set below (0x38 inquiry, 0x32 clock, 0x34 bitrate, 0x30 ID
authentication, 0x54 area inquiry, 0x12 erase, 0x13 write, 0x15 read) is the
serial boot-mode protocol published in the RX65N hardware manual. The E2 OB
only carries these bytes; see e2ob.py for the carrier.
"""

import struct

from e2ob import SOD, E2Error, build, parse

# Host-side commands
C_INQUIRY = 0x38       # device / clock inquiry
C_CLOCK = 0x32         # set input and system clock
C_BITRATE = 0x34       # set UART bitrate
C_STATUS = 0x00        # status inquiry; required before ID authentication
C_ID_METHOD = 0x2C     # ID authentication method inquiry
C_ID_AUTH = 0x30       # ID code authentication
C_CLOCK_MODE = 0x36    # select clock mode; must precede 0x32
C_AREA = 0x54          # memory area inquiry
C_ERASE = 0x12         # block erase
C_WRITE = 0x13         # write
C_READ = 0x15          # read

AREA_KINDS = {0x00: "Code Flash", 0x20: "Data Flash", 0x30: "Config Area"}

# The probe's own UART rate is set with E2 command 0x62 as a divisor of a
# 3 MHz reference (with the MCU at exactly 125000 bps, divisor 24 read 101376
# bytes with no gaps and no errors).
#
# So the probe can only be set to 3e6/n. 115200 is not on that grid: the
# nearest is 3e6/26 = 115385, which works only if the MCU is within about 2% of
# it, so the MCU needs its PLL (PCLKB at 48 MHz) rather than the plain 16 MHz
# HOCO; 125000 is exact on both sides without one.
#
# Changing the divisor part-way through a session gave inconsistent results,
# because data received at the old rate is still buffered, so a session sets it
# once. Rates other than these are refused rather than guessed at.
LINK_REFERENCE_HZ = 3000000
LINK_DIVISOR = {
    9600: 0x0139,      # 3e6/313 = 9585, 0.16% low
    115200: 26,        # 3e6/26 = 115385; MCU needs PCLKB at 48 MHz to match
    125000: 24,        # exact from HOCO alone, also confirmed loss-free
    1000000: 0x0003,   # 3e6/3
}


class Area:
    def __init__(self, kind, start, end, block, unit):
        self.kind, self.start, self.end = kind, start, end
        self.block, self.unit = block, unit

    @property
    def name(self):
        return AREA_KINDS.get(self.kind, "kind 0x%02X" % self.kind)

    def contains(self, addr):
        return self.start <= addr <= self.end

    def __str__(self):
        return "%08X-%08X  %-11s block=%d write unit=%d" % (
            self.start, self.end, self.name, self.block, self.unit)


class RXBoot:
    """One boot-mode session. `link` is an E2OB instance."""

    def __init__(self, link, verbose=False):
        self.link = link
        self.verbose = verbose
        self.areas = []

    # --- framing over the pass-through ---

    def _read_frame(self):
        """Read a response frame: 6-byte head first, then the remainder."""
        head = self.link.transfer(b"", 6)
        if len(head) < 6:
            raise E2Error("no response from MCU (%s)" % head.hex(" "))
        total = struct.unpack(">H", head[1:3])[0] + 5
        frame = head
        if total > len(frame):
            frame += self.link.read_more(total - len(frame))
        return frame

    def _send(self, cmd, data=b""):
        """Send a command frame and read its status/ACK frame."""
        req = build(cmd, data)
        head = self.link.transfer(req, 6)
        if len(head) < 6:
            raise E2Error("no ACK for command 0x%02X" % cmd)
        total = struct.unpack(">H", head[1:3])[0] + 5
        frame = head if total <= len(head) else head + self.link.read_more(total - len(head))
        rcmd, payload = parse(frame)
        if rcmd == cmd | 0x80:
            code = "0x%02X" % payload[0] if payload else "unknown"
            raise E2Error("command 0x%02X rejected, error %s" % (cmd, code))
        return frame, payload

    def _request_data(self, frame):
        """Ask for the data belonging to the ACK just received.

        The host echoes the ACK frame back (0x81-headed) and the MCU answers
        with the data frame.
        """
        head = self.link.transfer(frame, 6)
        total = struct.unpack(">H", head[1:3])[0] + 5
        full = head if total <= len(head) else head + self.link.read_more(total - len(head))
        return parse(full)[1]

    def exchange(self, cmd, data=b""):
        """Send a command, then fetch its data response."""
        ack, _ = self._send(cmd, data)
        return self._request_data(ack)

    # --- session setup ---

    def sync(self, tries=30):
        """Bitrate auto-detection: 0x00 bytes, then 0x55, expect 0xC2."""
        self.link.set_timeout(300)
        for _ in range(tries):
            try:
                self.link.transfer(b"\x00" * 10, 1)
            except E2Error:
                pass
            try:
                if self.link.transfer(b"\x55", 1) == b"\xc2":
                    self.link.set_timeout(5000)
                    return
            except E2Error:
                continue
        raise E2Error("no 0xC2 reply; MCU is not in boot mode")

    def inquiry(self):
        return self.exchange(C_INQUIRY)

    def set_clock_mode(self, mode=1):
        """0x36: select the clock mode. The MCU rejects 0x32 until this is set."""
        self._send(C_CLOCK_MODE, bytes((mode,)))

    def set_clock(self, input_hz=16000000, system_hz=120000000):
        """0x32: input clock then system clock, each a big-endian 32-bit Hz value.

        This command answers with a data frame as well as an ACK; the data has
        to be collected or the next command desynchronises the stream.
        """
        return self.exchange(C_CLOCK, struct.pack(">II", input_hz, system_hz))

    def set_bitrate(self, bps=1000000):
        """0x34: bitrate as a big-endian 32-bit value.

        The MCU acknowledges at the old rate and then switches, so the probe's
        own UART has to follow.
        """
        if bps not in LINK_DIVISOR:
            raise E2Error("bitrate %d is not one of the rates known to work here: %s"
                          % (bps, ", ".join(str(k) for k in sorted(LINK_DIVISOR))))
        self._send(C_BITRATE, struct.pack(">I", bps))
        self.link.command(0x62, struct.pack(">HH", 0, LINK_DIVISOR[bps]))

    def status(self):
        """0x00: status inquiry. The MCU rejects 0x30 until this has been sent."""
        self._send(C_STATUS)

    def id_method(self):
        """0x2C: which ID authentication method the MCU expects."""
        return self.exchange(C_ID_METHOD)

    def authenticate(self, id_code=b"\xff" * 16):
        if len(id_code) != 16:
            raise ValueError("ID code must be 16 bytes")
        self.status()
        method = self.id_method()
        self._send(C_ID_AUTH, id_code)
        return method

    def read_areas(self, max_areas=8):
        """0x54: walk the area table until the MCU stops describing new areas."""
        self.areas = []
        for i in range(max_areas):
            try:
                d = self.exchange(C_AREA, bytes((i,)))
            except E2Error:
                break
            if len(d) < 17:
                break
            kind = d[0]
            start, end, block, unit = struct.unpack(">IIII", d[1:17])
            if start == 0 and end == 0:
                break
            self.areas.append(Area(kind, start, end, block, unit))
        return self.areas

    def connect(self, id_code=b"\xff" * 16, input_hz=16000000, system_hz=120000000,
                bps=1000000, clock_mode=1):
        self.sync()
        info = self.inquiry()
        self.set_clock_mode(clock_mode)
        self.set_clock(input_hz, system_hz)
        self.set_bitrate(bps)
        self.authenticate(id_code)
        self.read_areas()
        return info

    # --- flash operations ---

    def area_for(self, addr):
        for a in self.areas:
            if a.contains(addr):
                return a
        raise E2Error("address %08X is not in any known area" % addr)

    def plan(self, chunks):
        """Merge loaded chunks into runs that can be erased and written safely.

        Two things make a chunk-by-chunk write wrong. Chunks that share an
        erase block would have the earlier one erased again by the later one,
        and a chunk rarely starts on a write-unit boundary. So chunks are
        merged per memory area, gaps inside a run are filled with 0xFF (which
        is what an erased block holds anyway), and each run is widened to the
        write unit.
        """
        by_area = {}
        for addr, data in chunks:
            area = self.area_for(addr)
            if not area.contains(addr + len(data) - 1):
                raise E2Error("%08X-%08X straddles a memory area boundary"
                              % (addr, addr + len(data) - 1))
            by_area.setdefault(id(area), (area, []))[1].append((addr, data))

        runs = []
        for area, group in by_area.values():
            group.sort()
            low = group[0][0]
            high = max(a + len(d) for a, d in group)
            low -= (low - area.start) % area.unit
            pad = (high - area.start) % area.unit
            if pad:
                high += area.unit - pad
            image = bytearray(b"\xff" * (high - low))
            for addr, data in group:
                image[addr - low:addr - low + len(data)] = data
            runs.append((low, bytes(image), area))
        runs.sort()
        return runs

    def erase(self, block_addr):
        """0x12: erase the single block whose start address is `block_addr`."""
        self._send(C_ERASE, struct.pack(">I", block_addr))

    def erase_range(self, start, end, progress=None):
        """Erase every block that overlaps start..end (inclusive)."""
        self.link.set_timeout(90000)   # a block erase takes far longer than a command
        area = self.area_for(start)
        first = start - (start - area.start) % area.block
        done = []
        addr = first
        while addr <= end:
            self.erase(addr)
            done.append(addr)
            if progress:
                progress(addr)
            addr += area.block
        return done

    def write(self, addr, data, progress=None):
        """Write `data` at `addr`, padding the tail to the area's write unit."""
        area = self.area_for(addr)
        if addr % area.unit:
            raise E2Error("address %08X is not aligned to write unit %d"
                          % (addr, area.unit))
        if len(data) % area.unit:
            data = data + b"\xff" * (area.unit - len(data) % area.unit)
        end = addr + len(data) - 1
        self.link.set_timeout(90000)
        ack, _ = self._send(C_WRITE, struct.pack(">II", addr, end))
        sent = 0
        while sent < len(data):
            part = data[sent:sent + 1024]
            last = sent + len(part) >= len(data)
            frame = build(C_WRITE, part, head=SOD, last=last)
            head = self.link.transfer(frame, 6)
            total = struct.unpack(">H", head[1:3])[0] + 5
            reply = head if total <= len(head) else head + self.link.read_more(total - len(head))
            rcmd, payload = parse(reply)
            if rcmd == C_WRITE | 0x80:
                code = "0x%02X" % payload[0] if payload else "unknown"
                raise E2Error("write rejected at %08X, error %s" % (addr + sent, code))
            sent += len(part)
            if progress:
                progress(sent, len(data))
        return sent

    def read(self, addr, length):
        """Read `length` bytes starting at `addr`."""
        out = bytearray()
        while len(out) < length:
            want = min(1024, length - len(out))
            start = addr + len(out)
            ack, _ = self._send(C_READ, struct.pack(">II", start, start + want - 1))
            out += self._request_data(ack)
        return bytes(out[:length])

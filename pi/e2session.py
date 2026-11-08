"""Bring the E2 OB up, put the MCU into boot mode, and release it again.

The 0x28 control commands here use the argument values that work on the RX65N
Envision Kit, and are described by what they do. The probe's command set is
from independent investigation; it is not documented by the vendor.
"""

import time

from e2ob import E2OB, E2Error

# Start-up sequence. Each entry is (command, payload, what it does).
INIT = [
    (0x06, "00 01 00 00", "open session"),
    (0x1D, "00", "select mode 0"),
    (0x6B, "00 00 13 88", "timeout 5000 ms"),
    (0x00, "", "probe status"),
    (0xD0, "00 00 00 00 00 01 00 00 00 01 ff fe 80 06", "firmware/version handshake"),
    (0x1D, "02", "select mode 2"),
    (0x00, "", "probe status"),
    (0x66, "07 40 08 40 03 40 05 40 07 28 20 00 08 39 40", "pin/interface assignment"),
    (0x00, "", "probe status"),
    (0x65, "", "read capabilities"),
    (0x61, "00 01", "target power on"),
    (0x62, "00 00 01 39", "probe UART rate: 3e6/313 = 9600 baud"),
    (0x6C, "", "read timeout settings"),
]

# 0x62 sets the probe's own UART rate; 0x0139 is 9600, which is what boot mode
# starts at. See LINK_DIVISOR in rxboot.py for the rest.
UART_9600 = "00 00 01 39"
RESET_ASSERT = "0f ff 04 00 00 00"   # drive mode/reset pins
RESET_RELEASE = "0f ff 00 00 00 00"  # release all pins -> user code runs


def _hex(s):
    return bytes.fromhex(s) if s else b""


class E2Session:
    """Manages the E2 OB around a boot-mode session."""

    def __init__(self, verbose=False):
        self.link = E2OB(verbose=verbose)
        self.verbose = verbose
        self.info = None
        self.capabilities = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.disconnect()
        except Exception:
            pass
        self.link.close()

    def start(self):
        """Run the start-up sequence and leave the MCU held in reset."""
        for cmd, payload, note in INIT:
            if self.verbose:
                print("E2 0x%02X  %s" % (cmd, note))
            reply = self.link.command(cmd, _hex(payload))
            if cmd == 0x00:
                self.info = reply
            elif cmd == 0x65:
                self.capabilities = reply
        return self.info

    def enter_boot_mode(self, settle=0.5):
        """Power-cycle the target so it samples the mode pins and enters boot mode.

        Without the power cycle the MCU keeps running whatever was flashed
        before and never answers the boot-mode sync.
        """
        self.link.command(0x61, _hex("00 00"))          # target power off
        time.sleep(settle)
        self.link.command(0x61, _hex("00 01"))          # target power on
        time.sleep(0.2)
        self.link.command(0x62, _hex(UART_9600))        # boot mode starts at 9600
        time.sleep(0.2)
        self.link.set_timeout(300)

    def run(self):
        """Release the mode and reset pins so the MCU executes user code."""
        self.link.command(0x64, _hex(RESET_ASSERT))
        self.link.command(0x64, _hex(RESET_RELEASE))

    def reset(self):
        """Assert reset without releasing the mode pins."""
        self.link.command(0x64, _hex(RESET_ASSERT))

    def disconnect(self):
        """Close the session, leaving the pins as the last run/reset call set them."""
        self.link.command(0x1D, b"\x00")
        self.link.command(0x0C, b"\x00\x00")

    def describe(self):
        """A short readable summary of the probe's status block."""
        if not self.info:
            return "no status read"
        return "status %d bytes: %s" % (len(self.info), self.info[:16].hex(" "))

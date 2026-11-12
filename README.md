# rx65n-web-writer

**▶ [Open the web page](https://paijp.github.io/rx65n-web-writer/)** -
connect to the E2 Lite of an RX65N board (such as the RX65N Envision Kit)
from Chrome or Edge, write a sample or your own `.mot`, and watch its serial
output. Nothing to install.

Write programs to an RX65N board (RX65N Envision Kit) through its on-board
E2 emulator Lite, run them, and watch their serial output - from a browser
tab, or from a Raspberry Pi.

> **本ソフトウェアはルネサス エレクトロニクスの公式ソフトウェアではありません。
> ルネサス エレクトロニクスが認定、保証またはサポートするものではありません。
> 本ソフトウェアに関するお問い合わせをルネサス エレクトロニクスに行わないでください。**
>
> This software is not official Renesas Electronics software. It is not
> certified, warranted or supported by Renesas Electronics. Please do not
> contact Renesas Electronics about it.

The probe's communication method is from independent investigation.

Use at your own risk: writing flash can leave a board in a state you have to
recover from. Option-setting memory is never written by either tool.

## Web page

Open <https://paijp.github.io/rx65n-web-writer/> in Chrome (or Edge), connect the board with a USB
cable (on Android, through USB OTG), and:

1. **Connect board** and pick the E2 Lite. The browser remembers it, so later
   visits connect by themselves.
2. Pick a sample from the list, or a `.mot` file of your own, and
   **Write & run**: the page puts the MCU in boot mode, erases, writes,
   verifies, then resets it and shows its serial output (SCI1, 115200 bps).
3. Type in the box to send text to the board (with a newline).
   **Reset & run** restarts the program, **Hold in reset** stops it, and
   **Save log** saves what was received.

Samples on the site, built from source by this repository's workflow:

| Sample | What it does |
|---|---|
| sertest flood | numbered lines as fast as the line goes - a gap in the numbers is a lost byte |
| sertest echo | sends back whatever you type |
| sample1 | a touch-panel UI on the LCD, logging each touch ([smallest-touchpanel-ui](https://github.com/paijp/smallest-touchpanel-ui)) |

`web/rx65n-web-writer.html` is the same page as one file, to download and
open from disk (desktop Chrome/Edge). Android Chrome generally cannot open a
downloaded HTML file, so use the site there.

Tested with Chrome on Android (Android 16). Not tested on a PC; on Windows the
E2 Lite is probably bound to the vendor's driver, which WebUSB cannot open.
WebUSB is not available in Firefox or Safari.

### Driven by Claude (optional, needs your own server)

The same page can be driven from a server: a small relay (`web/server.py`)
and its command-line side (`web/rx65nctl`) let an AI assistant with shell
access to your server - such as Claude through a command connector - write
the programs it generates to the board on your desk, read their output and
fix them, while you only keep the tab open. See [web/README.md](web/README.md).

## Raspberry Pi (Python)

`pi/rxflash.py` does the same from the command line, with pyusb:

```
sudo pip install pyusb            # or: apt install python3-usb
sudo python3 pi/rxflash.py info                     probe status and the MCU's memory areas
sudo python3 pi/rxflash.py write FILE.mot --log     erase, write, verify, run, show the output
sudo python3 pi/rxflash.py log                      reset and show the output
sudo python3 pi/rxflash.py term                     two-way terminal
sudo python3 pi/rxflash.py read ADDR LEN [-o FILE]  read flash
sudo python3 pi/rxflash.py verify FILE.mot          compare flash with an image
sudo python3 pi/rxflash.py run                      release reset
```

It runs anywhere pyusb does (Linux on x86 or ARM).

## Building the samples

`firmware/` builds the sertest images (`src/sertest.c`) and sample1 with GCC
for RX. `.github/workflows/pages.yml` builds binutils, GCC 16.1 and newlib for
`rx-elf` (cached after the first run), builds the samples and publishes them
with the page.

To build the same toolchain by hand (Linux on x86_64 or ARM, such as a
Raspberry Pi - there it takes an hour or more):

```
sudo apt install build-essential curl libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex
bash firmware/build-toolchain.sh          # installs to /opt/rx-elf (PREFIX=... to change)
export PATH=/opt/rx-elf/bin:$PATH
```

Then build the samples:

```
cd firmware
bash fetch-startup.sh            # the generated startup files (not in this repository)
make FLOOD=1 BAUD=115200         # sertest flood -> sertest.mot
bash fetch-lcdtp.sh && make sample1.mot
```

`firmware/Containerfile` builds the same with Renesas' GNURX toolchain
mounted into a container.

## Repository

| Path | What |
|---|---|
| `web/` | the page (`index.html`, `app.js`), its one-file copy, the optional relay |
| `pi/` | the Python tool |
| `firmware/` | sample sources and build |
| `tests/` | headless test of the page against a simulated probe (`node tests/run.mjs`) |

Issues are for bug reports in this software only. Questions about Renesas
products belong to Renesas' own channels; about this software, please do not
contact Renesas.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE), which also
covers the third-party material in the sample images.

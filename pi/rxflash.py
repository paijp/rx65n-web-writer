#!/usr/bin/env python3
"""rxflash - unofficial flash tool and serial monitor for RX65N boards with an
on-board E2 OB.

NOT official Renesas Electronics software, and not certified, warranted or
supported by Renesas Electronics. The probe's communication method is from
independent investigation. Writing flash can leave a board unresponsive; use at
your own risk. Option-setting memory is never written.

  rxflash.py info                     read probe status and memory areas
  rxflash.py read ADDR LEN [-o FILE]  read flash
  rxflash.py write FILE [--run|--log] erase, write and verify
  rxflash.py run                      release reset and let user code run
  rxflash.py log                      stream the MCU's serial output
  rxflash.py term                     two-way terminal over the same link
"""

import argparse
import sys
import time

from e2ob import E2Error
from e2session import E2Session
from rxboot import RXBoot
import monitor
import srec


def id_code(text):
    raw = bytes.fromhex(text.replace("_", "").replace("-", ""))
    if len(raw) != 16:
        raise argparse.ArgumentTypeError("ID code must be 32 hex digits (16 bytes)")
    return raw


def connect(session, args):
    session.enter_boot_mode()
    boot = RXBoot(session.link, verbose=args.verbose)
    info = boot.connect(id_code=args.id, input_hz=args.input_clock,
                        system_hz=args.system_clock, bps=args.bitrate)
    return boot, info


def cmd_info(args):
    with E2Session(verbose=args.verbose) as s:
        s.start()
        print(s.describe())
        if s.capabilities:
            print("capabilities: %s" % s.capabilities[:12].hex(" "))
        boot, info = connect(s, args)
        print("device inquiry: %s" % info[:24].hex(" "))
        print("memory areas:")
        for a in boot.areas:
            print("  %s" % a)
    return 0


def cmd_read(args):
    with E2Session(verbose=args.verbose) as s:
        s.start()
        boot, _ = connect(s, args)
        data = boot.read(args.address, args.length)
        if args.output:
            open(args.output, "wb").write(data)
            print("wrote %d bytes to %s" % (len(data), args.output))
        else:
            for off in range(0, len(data), 16):
                print("%08X  %s" % (args.address + off, data[off:off + 16].hex(" ")))
    return 0


def cmd_verify(args):
    """Compare flash against an image without erasing or writing anything.

    `write` already reads back everything it wrote and compares it, so this is
    not for checking that programming worked. It is for asking the question
    afterwards: the board has been running, and has stopped - are the bytes in
    flash still the ones that were put there? A program that reaches the flash
    control unit by accident can erase or rewrite its own code, which would look
    exactly like an intermittent stop and leave no other trace. Entering boot
    mode power-cycles the target, so this runs after the fact rather than while
    the program is stopped.

    Unlike the verify inside `write`, this reports every run that differs and how
    many bytes rather than stopping at the first, because for a post-mortem the
    pattern of the damage is the information.
    """
    chunks = srec.load(args.file, args.base)
    print("comparing flash against %s:" % args.file)
    print(srec.summarise(chunks))

    with E2Session(verbose=args.verbose) as s:
        s.start()
        boot, _ = connect(s, args)
        bad = 0
        for addr, data, area in boot.plan(chunks):
            back = boot.read(addr, len(data))
            diff = [i for i in range(len(data)) if back[i] != data[i]]
            if not diff:
                print("  %08X-%08X  %d bytes  ok" % (addr, addr + len(data) - 1, len(data)))
                continue
            bad += len(diff)
            print("  %08X-%08X  %d of %d bytes DIFFER, first at %08X: "
                  "image %02X, flash %02X"
                  % (addr, addr + len(data) - 1, len(diff), len(data),
                     addr + diff[0], data[diff[0]], back[diff[0]]))
            for i in diff[:16]:
                print("    %08X  image %02X  flash %02X" % (addr + i, data[i], back[i]))
            if len(diff) > 16:
                print("    ... and %d more" % (len(diff) - 16))
    if bad:
        print("%d bytes differ" % bad)
        return 1
    print("flash matches the image")
    return 0


def cmd_write(args):
    chunks = srec.load(args.file, args.base)
    print("loaded %s:" % args.file)
    print(srec.summarise(chunks))

    with E2Session(verbose=args.verbose) as s:
        s.start()
        boot, _ = connect(s, args)

        runs = boot.plan(chunks)
        print("write plan:")
        for addr, data, area in runs:
            print("  %08X-%08X  %d bytes  (%s)"
                  % (addr, addr + len(data) - 1, len(data), area.name))

        for addr, data, area in runs:
            # Never written: option-setting memory holds settings that can
            # change how the MCU starts or make it hard to recover. Strip the
            # .ofs sections from the image (objcopy -R .ofs1 ... -R .ofs7).
            if area.kind == 0x30 and not all(b == 0xFF for b in data):
                raise SystemExit(
                    "refusing to write %08X: that is option-setting memory, which "
                    "this tool does not write. Remove the .ofs sections from the "
                    "image." % addr)

        # All-FF records there (the unprogrammed value) are simply dropped.
        runs = [r for r in runs if r[2].kind != 0x30]
        started = time.time()
        total = sum(len(d) for _, d, _ in runs)
        for addr, data, area in runs:
            end = addr + len(data) - 1
            print("erase  %08X-%08X" % (addr, end))
            boot.erase_range(addr, end)
            print("write  %08X-%08X" % (addr, end))

            def show(done, size):
                sys.stdout.write("\r  %3d%%  %d/%d bytes"
                                 % (100 * done // size, done, size))
                sys.stdout.flush()

            boot.write(addr, data, progress=show)
            sys.stdout.write("\n")

            if not args.no_verify:
                print("verify %08X-%08X" % (addr, end))
                back = boot.read(addr, len(data))
                if back != data:
                    bad = next(i for i in range(len(data)) if back[i] != data[i])
                    raise SystemExit("verify failed at %08X: wrote %02X, read %02X"
                                     % (addr + bad, data[bad], back[bad]))
                print("  ok")

        print("done, %d bytes in %.1f s" % (total, time.time() - started))
        if args.run or args.log:
            print("releasing reset")
            s.run()
        if args.log:
            print("--- log (%d baud), Ctrl-C to stop ---" % args.rate)
            try:
                monitor.stream(s.link, bps=args.rate,
                               on_status=lambda st: sys.stderr.write(
                                   "[receive error %s]\n" % st.hex(" ")))
            except KeyboardInterrupt:
                print("\n--- stopped ---")
    return 0


def cmd_log(args):
    with E2Session(verbose=args.verbose) as s:
        s.start()
        if args.reset:
            monitor.drain(s.link)          # discard the old program's output
            print("releasing reset")
            s.run()
        print("--- log (%d baud), Ctrl-C to stop ---" % args.rate)
        try:
            monitor.stream(s.link, bps=args.rate, block=args.block,
                           poll_ms=args.poll, exact=args.exact,
                           on_status=lambda st: sys.stderr.write(
                               "[receive error %s]\n" % st.hex(" ")))
        except KeyboardInterrupt:
            print("\n--- stopped ---")
    return 0


def cmd_term(args):
    with E2Session(verbose=args.verbose) as s:
        s.start()
        if args.reset:
            monitor.drain(s.link)          # discard the old program's output
            print("releasing reset")
            s.run()
        print("--- terminal (%d baud), Ctrl-C to stop ---" % args.rate)
        try:
            monitor.interact(s.link, bps=args.rate, block=args.block,
                             poll_ms=args.poll)
        except KeyboardInterrupt:
            print("\n--- stopped ---")
    return 0


def cmd_run(args):
    with E2Session(verbose=args.verbose) as s:
        s.start()
        s.run()
        print("reset released")
    return 0


NO_RESET_HELP = ("do not release reset; note that opening a probe session leaves "
                 "the MCU held, so without a reset there is no output")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                epilog="This is not official Renesas Electronics software and is not "
                                       "certified, warranted or supported by Renesas Electronics.",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true", help="log USB traffic")
    p.add_argument("--id", type=id_code, default=b"\xff" * 16, metavar="HEX",
                   help="16-byte ID code (default all FF)")
    p.add_argument("--input-clock", type=int, default=16000000,
                   help="input clock in Hz (default 16000000)")
    p.add_argument("--system-clock", type=int, default=120000000,
                   help="system clock in Hz (default 120000000)")
    p.add_argument("--bitrate", type=int, default=1000000,
                   help="boot-mode UART bitrate (default 1000000)")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("info", help="read probe status and memory areas")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("read", help="read flash")
    sp.add_argument("address", type=lambda x: int(x, 0))
    sp.add_argument("length", type=lambda x: int(x, 0))
    sp.add_argument("-o", "--output", help="write to a file instead of the terminal")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("verify", help="compare flash against an image, writing nothing")
    sp.add_argument("file", help=".mot/.srec S-record, or a raw binary with --base")
    sp.add_argument("--base", type=lambda x: int(x, 0),
                    help="load address for a raw binary")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("write", help="erase, write and verify")
    sp.add_argument("file", help=".mot/.srec S-record, or a raw binary with --base")
    sp.add_argument("--base", type=lambda x: int(x, 0),
                    help="load address, for a raw binary")
    sp.add_argument("--run", action="store_true", help="run user code afterwards")
    sp.add_argument("--log", action="store_true",
                    help="run user code and then stream its serial output")
    sp.add_argument("--rate", type=int, default=115200,
                    help="log baud rate (default 115200)")
    sp.add_argument("--no-verify", action="store_true", help="skip read-back")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("run", help="release reset and let user code run")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("term",
                        help="two-way terminal: stdin to the MCU, its output to stdout")
    sp.add_argument("--rate", type=int, default=115200,
                    help="baud rate (default 115200)")
    sp.add_argument("--no-reset", dest="reset", action="store_false",
                    help=NO_RESET_HELP)
    sp.add_argument("--block", type=int, default=256, help="read size per poll")
    sp.add_argument("--poll", type=int, default=50, help="poll timeout in ms")
    sp.set_defaults(func=cmd_term, reset=True)

    sp = sub.add_parser("log", help="stream the MCU's serial output")
    sp.add_argument("--rate", type=int, default=115200,
                    help="baud rate (default 115200)")
    sp.add_argument("--no-reset", dest="reset", action="store_false",
                    help=NO_RESET_HELP)
    sp.add_argument("--block", type=int, default=256, help="read size per poll")
    sp.add_argument("--poll", type=int, default=100, help="poll timeout in ms")
    sp.add_argument("--exact", action="store_true",
                    help="read a byte at a time; slower but never guesses where "
                         "the data ends, for binary output that may contain 0xFF")
    sp.set_defaults(func=cmd_log, reset=True)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (E2Error, srec.LoadError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

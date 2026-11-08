#!/bin/bash
# Fetch the RX65N touch-panel port that the sample1 demo is built from.
#
#   bash fetch-startup.sh      # once: the startup files
#   bash fetch-lcdtp.sh        # the port
#   make sample1.mot           # the UI demo, its log coming out of the cable
#
# Why fetch rather than copy: the port lives in paijp/smallest-touchpanel-ui and
# is that project's to change. It is Apache 2.0 with one exception -
# envision_hw.c and envision_hw.h are MIT, derived from EnvisionDemo1 - and both
# notices travel with the files. A second copy here would go stale and would put
# this repository in the business of maintaining someone else's licence headers.
#
# What it is doing in a flash tool's repository at all: it is the end-to-end
# demonstration. lcdtp_sendlogc() is the port's log sink, and on RX65N it writes
# to three places - a RAM ring buffer, the debugger console, and seriallog.c,
# which drives SCI1. SCI1 is the boot-mode serial the E2 OB is already wired to,
# so a program's own log comes back out of the cable that programmed it, with
# no adapter and no debugger:
#
#   sudo python3 pi/rxflash.py write firmware/sample1.mot --log   (or the web page)
#
# That is the thing this repository exists to make possible, running a real
# application rather than a test pattern.
set -euo pipefail

REPO="${REPO:-paijp/smallest-touchpanel-ui}"
# Override REF to build from another branch or tag.
REF="${REF:-main}"
UPSTREAM="https://raw.githubusercontent.com/$REPO/$REF/rx65n"

FILES="basic.h dbgcon.h debuglog.c debuglog.h envision_hw.c envision_hw.h
       i2c.h lcdtp.c lcdtp.h sample1.c seriallog.c seriallog.h tplib.c tplib.h"

mkdir -p lcdtp
for f in $FILES; do
    curl -fsSL -o "lcdtp/$f" "$UPSTREAM/$f"
done

echo "fetched into lcdtp/ from $REPO at $REF:"
ls lcdtp
echo "now: make sample1.mot"

#!/bin/bash
# Fetch the startup files the test firmware links against.
#
#   bash fetch-startup.sh
#
# generate/ is e2 studio's generated startup for the RX65N - reset code,
# interrupt vectors, register definitions and the linker script. It is taken
# from the MiniWin EnvisionDemo1 project (github.com/miniwinwm/RenesasEnvisionGCC)
# rather than copied into this repository. Those generated files carry Renesas
# Electronics' own notice (for use with Renesas products only), not MiniWin's
# MIT licence, so they are fetched at build time and never committed here;
# images built from them are only for running on Renesas MCUs. Only iodefine.h
# and the startup are used; none of the demo's own code is.
#
# The one change made here is the stack size. Upstream puts the user stack top
# at 0x200 with the interrupt stack directly below at 0x100, leaving 256 bytes,
# and its own README says to raise it to 0x500. The committed file still has the
# default, so the patch below applies what that README asks for.
set -euo pipefail

UPSTREAM="https://raw.githubusercontent.com/miniwinwm/RenesasEnvisionGCC/master/EnvisionDemo1/generate"
FILES="hwinit.c interrupt_handlers.h inthandler.c iodefine.h linker_script.ld start.S typedefine.h vects.c"

mkdir -p generate
for f in $FILES; do
    curl -fsSL -o "generate/$f" "$UPSTREAM/$f"
done

python3 - <<'PY'
path = "generate/linker_script.ld"
text = open(path).read()
before = text
text = text.replace(".ustack 0x200: AT(0x200)", ".ustack 0x500: AT(0x500)")
text = text.replace(".data 0x204", ".data 0x504")
if text == before:
    print("linker script: stack patch did not apply - check it by hand")
else:
    open(path, "w").write(text)
    print("linker script: user stack raised to 0x500 (1 KB)")
PY

echo "fetched into generate/:"
ls generate
echo "now: make"

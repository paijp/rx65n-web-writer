# Build the RX65N serial test firmware with GNU RX.
#
#   bash fetch-startup.sh        # once: fetch generate/ (startup + linker script)
#   make                         # -> sertest.mot, the echo build at 125000 bps
#   make FLOOD=1                 # -> sertest.mot, numbered lines back to back
#   make BAUD=9600               # a different rate
#
#   bash fetch-lcdtp.sh          # once more: the RX65N touch-panel port
#   make sample1.mot             # -> the UI demo, logging out of the cable
#
# generate/ is e2 studio's startup for this chip, taken from the MiniWin
# EnvisionDemo1 project rather than vendored here, so it stays under its
# upstream licence. fetch-startup.sh explains what it takes and why.

CC      := rx-elf-gcc
OBJCOPY := rx-elf-objcopy
SIZE    := rx-elf-size

# RX65N is an RXv2 core, but how to say so depends on the toolchain.
# -mcpu=rx64m is Renesas' own GNURX naming - the part numbers are their
# extension - and a toolchain built from upstream GCC rejects it, as it does
# some of the core names depending on version. Rather than encode a guess, try
# the spellings in order of preference and keep the first the compiler accepts;
# an empty CPU is the last resort and still produces a working image, just
# without RXv2 instructions. Override with CPU=<name>, or CPU= for none.
#
# Check what a given toolchain offers with
#   rx-elf-gcc --help=target | grep -A20 mcpu
CPU_CANDIDATES ?= rx64m rxv2 rx600
CPU     ?= $(firstword $(foreach c,$(CPU_CANDIDATES),\
             $(shell $(CC) -mcpu=$(c) -E -x c /dev/null >/dev/null 2>&1 && echo $(c))))

CPUFLAG := $(if $(CPU),-mcpu=$(CPU),)

BAUD    ?= 125000
FLOOD   ?=

# Debug info, on by default because gdb on the .elf is useful and the .mot does
# not carry it either way - the image is byte-identical with and without -g.
# Overridable because it can be the thing that breaks the build: upstream GCC 12
# to 15 for rx crash generating CFI for several of the touch-panel port's
# functions at -O0 -g ("internal compiler error: in
# dwarf2out_frame_debug_adjust_cfa, at dwarf2cfi.cc"), a compiler bug in code
# that only runs for -g. GCC 16.1 is the first that does not, and GNURX never
# did. With an affected compiler, build with DBG= to get past it.
DBG     ?= -g

DEFS    := -DBAUD=$(BAUD)
ifneq ($(FLOOD),)
DEFS    += -DFLOOD
endif

CFLAGS  := $(CPUFLAG) -O2 $(DBG) -std=gnu99 -nostartfiles -Igenerate -Isrc \
           -ffunction-sections -fdata-sections $(DEFS)
LDFLAGS := -T generate/linker_script.ld -Wl,--gc-sections -Wl,-Map=sertest.map

COMMON  := generate/hwinit.c \
           generate/inthandler.c \
           generate/vects.c

SRCS    := src/sertest.c $(COMMON) generate/start.S

# The .ofs* sections are option-setting memory. Neither the web page nor
# rxflash writes it, and their content is all-FF anyway - the same as an
# unprogrammed part - so dropping them costs nothing.
OFS     := -R .ofs1 -R .ofs2 -R .ofs3 -R .ofs4 -R .ofs5 -R .ofs6 -R .ofs7

all: sertest.mot

sertest.elf: $(SRCS)
	$(CC) $(CFLAGS) $^ $(LDFLAGS) -o $@
	$(SIZE) $@

sertest.mot: sertest.elf
	$(OBJCOPY) -O srec --srec-forceS3 $(OFS) $< $@
	@echo "built $@ ($$(wc -c < $@) bytes) at $(BAUD) bps$(if $(FLOOD), flood build,)"

# The touch-panel demo. Its log sink, lcdtp_sendlogc(), writes to seriallog.c
# as well as to the RAM ring buffer and the debugger console, so the program's
# own log comes back through the E2 OB:
#
#   sudo python3 pi/rxflash.py write firmware/sample1.mot --log
#
# -O2, like everything else here. The port used to insist on -O0, because
# optimised builds stopped within seconds - but that was measured while SCKCR was
# being written one field at a time, which left ICLK at 240 MHz, twice the
# maximum, so the flash could not be read reliably. With SCKCR written in one
# store, i2craw survived 8 of 8 interleaved runs at both -O0 and -O2, the old
# SCKCR write at -O2 survived 0 of 8, and sample1 at -O2 ran and logged every
# touch on the board - with GNURX 14.2 and with upstream GCC 16.1 alike.
LCDTP   := lcdtp/sample1.c lcdtp/tplib.c lcdtp/lcdtp.c lcdtp/envision_hw.c \
           lcdtp/debuglog.c lcdtp/seriallog.c
LCDCFLAGS := $(CPUFLAG) -O2 $(DBG) -std=gnu99 -nostartfiles -Igenerate -Ilcdtp \
             -fdata-sections -DSERIALLOG_BAUD=115200 \
             -Wno-error=incompatible-pointer-types

sample1.elf: $(LCDTP) $(COMMON) generate/start.S
	$(CC) $(LCDCFLAGS) $^ -T generate/linker_script.ld -Wl,-Map=sample1.map -o $@
	$(SIZE) $@

sample1.mot: sample1.elf
	$(OBJCOPY) -O srec --srec-forceS3 $(OFS) $< $@
	@echo "built $@ ($$(wc -c < $@) bytes); write it with --log to read its own log"

clean:
	rm -f *.elf *.mot *.map

.PHONY: all clean

# What the detection above settled on, for a build log to record.
print-cpu:
	@echo "$(if $(CPU),$(CPU),none)"

.PHONY: print-cpu

#!/bin/bash
# Build the rx-elf toolchain (binutils, a C-only gcc with libgcc, newlib) from
# the GNU sources - the same one .github/workflows/pages.yml builds and caches.
# Runs on any Linux with a native compiler, x86_64 or ARM (a Raspberry Pi
# included; expect an hour or more there).
#
#   Debian/Ubuntu:  sudo apt install build-essential curl libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex
#   then:           bash firmware/build-toolchain.sh          (installs to /opt/rx-elf)
#                   PREFIX=$HOME/rx-elf bash firmware/build-toolchain.sh
#   and:            export PATH=/opt/rx-elf/bin:$PATH
set -euo pipefail
BINUTILS=${BINUTILS:-2.43}
GCC=${GCC:-16.1.0}
NEWLIB=${NEWLIB:-4.4.0.20231231}
PREFIX=${PREFIX:-/opt/rx-elf}
SRC=${SRC:-$HOME/src/rx-elf}
JOBS=${JOBS:-$(nproc)}

if ! mkdir -p "$PREFIX" 2>/dev/null || [ ! -w "$PREFIX" ]; then
	sudo mkdir -p "$PREFIX"
	sudo chown "$(id -u)" "$PREFIX"
fi
mkdir -p "$SRC" && cd "$SRC"

[ -f "binutils-$BINUTILS.tar.xz" ] || curl -fsSLO "https://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS.tar.xz"
[ -f "gcc-$GCC.tar.xz" ] || curl -fsSLO "https://ftp.gnu.org/gnu/gcc/gcc-$GCC/gcc-$GCC.tar.xz"
[ -f "newlib-$NEWLIB.tar.gz" ] || curl -fsSLO "https://sourceware.org/pub/newlib/newlib-$NEWLIB.tar.gz"
rm -rf "binutils-$BINUTILS" "gcc-$GCC" "newlib-$NEWLIB" build-binutils build-gcc build-newlib
tar xf "binutils-$BINUTILS.tar.xz"
tar xf "gcc-$GCC.tar.xz"
tar xf "newlib-$NEWLIB.tar.gz"

mkdir build-binutils && cd build-binutils
"../binutils-$BINUTILS/configure" \
	--target=rx-elf --prefix="$PREFIX" \
	--disable-nls --disable-werror --with-sysroot
make -j"$JOBS"
make install
cd ..

export PATH="$PREFIX/bin:$PATH"

# Newlib is here for its headers, not its code: the firmware is built
# -nostartfiles and calls no libc function, but it includes stdint.h, and a
# gcc built --without-headers has none to offer. Hence the usual three-stage
# order: the compiler, then newlib with it, then libgcc against its headers.
mkdir build-gcc && cd build-gcc
"../gcc-$GCC/configure" \
	--target=rx-elf --prefix="$PREFIX" \
	--enable-languages=c --with-newlib \
	--disable-nls --disable-shared --disable-threads \
	--disable-libssp --disable-libgomp --disable-libquadmath \
	--disable-libatomic --disable-decimal-float --disable-libffi
make -j"$JOBS" all-gcc
make install-gcc
cd ..

mkdir build-newlib && cd build-newlib
"../newlib-$NEWLIB/configure" --target=rx-elf --prefix="$PREFIX"
make -j"$JOBS"
make install
cd ..

cd build-gcc
make -j"$JOBS" all-target-libgcc
make install-target-libgcc

"$PREFIX/bin/rx-elf-gcc" --version | head -1
echo "Done. Add $PREFIX/bin to PATH."

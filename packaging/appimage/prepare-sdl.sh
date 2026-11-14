#!/bin/sh
# Run ONLY inside the selected disposable build baseline, not on the host.
set -eu
if [ "$#" -ne 3 ]; then
    echo "Usage: prepare-sdl.sh SOURCE_TAR_GZ NEW_BUILD_DIRECTORY INSTALL_PREFIX" >&2
    exit 2
fi
archive=$1
work=$2
prefix=$3
printf '%s  %s\n' 4c3b09330d866dc52eb65b66259a6684ad387252ca8c57901b3a2b534eb42e3d "$archive" | sha256sum -c -
mkdir "$work"
tar -xzf "$archive" -C "$work"
cmake -S "$work/SDL3-3.2.30" -B "$work/build" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DSDL_AUDIO=OFF -DSDL_VIDEO=OFF -DSDL_UNIX_CONSOLE_BUILD=ON \
    -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF -DSDL_SHARED=ON -DSDL_STATIC=OFF
cmake --build "$work/build" -j6
cmake --install "$work/build"
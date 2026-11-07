#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

set -o pipefail
arch=$1
source_dir=$(realpath "$2")
output=$(realpath -m "$3")
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

case "$arch" in
x86_64) server_backend=enabled ;;
aarch64) server_backend=disabled ;;
*) exit 2 ;;
esac

meson setup "$work/build" "$source_dir" --prefix=/usr -Dsqlite3=enabled \
	-Dmysql="$server_backend" -Dpostgresql="$server_backend" \
	-Denable-arch=all -Dpcie-edpc=enabled -Dbmc-generic=enabled
ninja -C "$work/build"
meson install -C "$work/build" --destdir "$work/install"

mkdir -p "$output"
tar -cf "$output/rasdaemon-install.tar" -C "$work/install" .
cp "$work/build/config.h" "$output/config.h"
git -c safe.directory="$source_dir" -C "$source_dir" rev-parse HEAD > "$output/source-revision"
printf '%s\n' "$arch" > "$output/architecture"
python3 "$script_dir/build-key.py" rasdaemon > "$output/build-key"

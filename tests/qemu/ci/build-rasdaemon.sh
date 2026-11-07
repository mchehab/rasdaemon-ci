#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

set -o pipefail
arch=$1
revision=$2
output=$(realpath -m "$3")
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
server_deps=()

case "$arch" in
x86_64) server_deps=(libmariadb-dev libmariadb-dev-compat libpq-dev) ;;
aarch64) ;;
*) exit 2 ;;
esac

sudo apt-get update
sudo apt-get install -y build-essential git meson ninja-build pkg-config \
	libtraceevent-dev libsqlite3-dev libpci-dev jq python3 "${server_deps[@]}"
git init -q "$work/source"
git -C "$work/source" remote add origin https://github.com/mchehab/rasdaemon.git
git -C "$work/source" fetch -q --depth=1 origin "$revision"
git -C "$work/source" checkout -q --detach FETCH_HEAD
test "$(git -C "$work/source" rev-parse HEAD)" = "$revision"
settings=$(python3 "$script_dir/compilation.py" rasdaemon "$arch")
mapfile -t options < <(jq -r '.meson[]' <<< "$settings")
meson setup "$work/build" "$work/source" "${options[@]}"
ninja -C "$work/build"
meson install -C "$work/build" --destdir "$work/install"

mkdir -p "$output"
tar -cf "$output/rasdaemon-install.tar" -C "$work/install" .
cp "$work/build/config.h" "$output/config.h"
printf '%s\n' "$revision" > "$output/source-revision"
printf '%s\n' "$arch" > "$output/architecture"
python3 "$script_dir/compilation.py" rasdaemon "$arch" --key > "$output/build-key"

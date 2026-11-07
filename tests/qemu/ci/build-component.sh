#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

set -o pipefail
component=$1
arch=$2
revision=$3
output=$4
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
case "$arch" in x86_64|aarch64) ;; *) exit 2 ;; esac

case "$component" in
kernel)
	sudo apt-get update
	sudo apt-get install -y build-essential bc bison flex dwarves libelf-dev libssl-dev git jq python3
	"$script_dir/build-kernel.sh" --arch "$arch" --ref "$revision" --output "$output"
	;;
qemu)
	sudo apt-get update
	sudo apt-get install -y build-essential git meson ninja-build pkg-config \
		python3 python3-venv jq libglib2.0-dev libpixman-1-dev libslirp-dev \
		libfdt-dev libaio-dev liburing-dev libcap-ng-dev libseccomp-dev \
		libgnutls28-dev libudev-dev
	"$script_dir/build-qemu.sh" --ref "$revision" --output "$output"
	;;
rasdaemon)
	"$script_dir/build-rasdaemon.sh" "$arch" "$revision" "$output"
	;;
*) echo "Unknown component: $component" >&2; exit 2 ;;
esac

# Retain the exact settings with the compiled output for later inspection.
mkdir -p "$output/metadata"
python3 "$script_dir/compilation.py" "$component" "$arch" > "$output/metadata/compilation.json"
python3 "$script_dir/compilation.py" "$component" "$arch" --key > "$output/metadata/build-key"

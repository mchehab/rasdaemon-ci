#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

kernel=
output=
base_url=https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
base_sha512=
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
guest_dir=$(realpath "$script_dir/../guest")

while test $# -gt 0; do
	case $1 in
	--kernel) kernel=$2; shift 2 ;;
	--output) output=$2; shift 2 ;;
	--base-url) base_url=$2; shift 2 ;;
	--base-sha512) base_sha512=$2; shift 2 ;;
	*) echo "Unknown argument: $1" >&2; exit 2 ;;
	esac
done

test -d "$kernel" || { echo "--kernel must name a build directory: $kernel" >&2; exit 2; }
test -n "$output" || { echo "--output is required" >&2; exit 2; }
command -v virt-customize >/dev/null || {
	echo "virt-customize is not installed" >&2
	exit 1
}
command -v virt-sparsify >/dev/null || {
	echo "virt-sparsify is not installed" >&2
	exit 1
}

release=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' \
	"$kernel/metadata/kernel.json")
module_dir="$kernel/modules/lib/modules/$release"
test -d "$module_dir" || {
	echo "kernel modules directory is missing: $module_dir" >&2
	find "$kernel" -maxdepth 5 -type d -print >&2
	exit 1
}

work=$(mktemp -d "${TMPDIR:-/tmp}/ras-guest-build.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
base="$work/base.qcow2"
curl --fail --location --retry 3 "$base_url" --output "$base"

# The official directory publishes the checksum next to the mutable latest URL.
sum_url=$(dirname "$base_url")/SHA512SUMS
filename=$(basename "$base_url")
expected=$base_sha512
if test -z "$expected"; then
	expected=$(curl --fail --location --retry 3 "$sum_url" | \
		awk -v file="$filename" '$2 == file {print $1; exit}')
fi
test -n "$expected" || { echo "No checksum for $filename" >&2; exit 1; }
printf '%s  %s\n' "$expected" "$base" | sha512sum --check

cp "$base" "$work/custom.qcow2"
virt-customize -a "$work/custom.qcow2" \
	--install libtraceevent1,libsqlite3-0,sqlite3,libpci3,python3,python3-sqlalchemy,util-linux,initramfs-tools \
	--mkdir /usr/local/libexec \
	--copy-in "$guest_dir/bootstrap.sh:/usr/local/libexec" \
	--copy-in "$guest_dir/ras-qemu-agent.service:/etc/systemd/system" \
	--copy-in "$kernel/vmlinuz-$release:/boot" \
	--copy-in "$kernel/config-$release:/boot" \
	--copy-in "$kernel/System.map-$release:/boot" \
	--copy-in "$module_dir:/lib/modules" \
	--run-command 'chmod 0755 /usr/local/libexec/bootstrap.sh' \
	--run-command 'ln -sf bootstrap.sh /usr/local/libexec/ras-qemu-agent' \
	--run-command "depmod '$release'" \
	--run-command "update-initramfs -c -k '$release'" \
	--run-command 'systemctl enable ras-qemu-agent.service' \
	--run-command 'systemctl mask apt-daily.service apt-daily-upgrade.service apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true' \
	--run-command 'apt-get clean' \
	--run-command 'rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*' \
	--run-command 'update-grub'

mkdir -p "$(dirname "$output")"
virt-sparsify --compress "$work/custom.qcow2" "$output"
sha256sum "$output" >"$output.sha256"
printf '%s\n' "$base_url" >"$output.base-url"
printf '%s\n' "$expected" >"$output.base-sha512"

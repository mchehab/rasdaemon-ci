#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

set -o pipefail

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
kernel=$(realpath "$kernel")
output=$(realpath -m "$output")
command -v qemu-system-x86_64 >/dev/null || {
	echo "qemu-system-x86_64 is not installed" >&2
	exit 1
}
command -v qemu-img >/dev/null || {
	echo "qemu-img is not installed" >&2
	exit 1
}
command -v mkfs.ext4 >/dev/null || {
	echo "mkfs.ext4 is not installed" >&2
	exit 1
}
command -v blkid >/dev/null || {
	echo "blkid is not installed" >&2
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
# The checksum is resolved by the caller immediately before this download.
# `latest` is mutable, and CDN edges can otherwise serve the previous qcow2
# after the checksum file has been updated.  Make a supplied digest part of
# the request URL so that the image is fetched independently of that stale
# cache entry.
download_url=$base_url
if test -n "$base_sha512"; then
	case $base_url in
	*\?*) download_url="$base_url&sha512=$base_sha512" ;;
	*) download_url="$base_url?sha512=$base_sha512" ;;
	esac
fi
curl --fail --location --retry 3 "$download_url" --output "$base"

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
payload="$work/payload"
mkdir -p "$payload/modules/lib/modules" "$payload/guest"
cp "$kernel/vmlinuz-$release" "$payload/"
cp "$kernel/config-$release" "$payload/"
cp "$kernel/System.map-$release" "$payload/"
cp -a "$module_dir" "$payload/modules/lib/modules/"
cp "$guest_dir/bootstrap.sh" "$payload/guest/"
cp "$guest_dir/ras-qemu-agent.service" "$payload/guest/"
cp "$guest_dir/select-kernel.sh" "$payload/guest/"
cp "$guest_dir/install-kernel.sh" "$payload/guest/"

payload_size=$(du -sm "$payload" | awk '{print $1 + 64}')
payload_image="$work/payload.ext4"
truncate -s "${payload_size}M" "$payload_image"
mkfs.ext4 -F -q -L RASDAEMON_CI -d "$payload" "$payload_image"
payload_uuid=$(blkid -s UUID -o value "$payload_image")
test -n "$payload_uuid"

cat >"$work/user-data" <<EOF
#cloud-config
package_update: true
packages:
  - initramfs-tools
  - libpci3
  - pciutils
  - libsqlite3-0
  - libtraceevent1
  - python3
  - python3-sqlalchemy
  - sqlite3
  - util-linux
runcmd:
  - [ bash, -c, "set -euxo pipefail; device=\$(blkid -U '$payload_uuid'); test -b \"\$device\"; mkdir -p /mnt/payload; mount -o ro \"\$device\" /mnt/payload; bash /mnt/payload/guest/install-kernel.sh '$release'" ]
power_state:
  mode: poweroff
  timeout: 30
EOF
cat >"$work/meta-data" <<EOF
instance-id: rasdaemon-ci-$release
local-hostname: rasdaemon-ci
EOF
command -v cloud-localds >/dev/null || {
	echo "cloud-localds is not installed" >&2
	exit 1
}
cloud-localds "$work/seed.iso" "$work/user-data" "$work/meta-data"
console="$work/console.log"
cleanup_guest_build() {
	status=$?
	if test "$status" -ne 0 && test -f "$console"; then
		mkdir -p "$(dirname "$output")"
		cp "$console" "$output.console.log"
	fi
	rm -rf "$work"
	exit "$status"
}
trap cleanup_guest_build EXIT HUP INT TERM
accel=tcg
if test -r /dev/kvm -a -w /dev/kvm; then
	accel=kvm
fi
echo "guest provisioning accelerator: $accel"
timeout 20m qemu-system-x86_64 -machine "q35,accel=$accel" -m 2048 -smp 2 -display none \
	-no-reboot -serial "file:$console" \
	-drive "file=$work/custom.qcow2,if=virtio,format=qcow2" \
	-drive "file=$work/seed.iso,if=virtio,format=raw,readonly=on" \
	-drive "file=$payload_image,if=virtio,format=raw,readonly=on" \
	-nic user,model=virtio || {
	echo "guest provisioning failed" >&2
	tail -n 100 "$console" >&2 || true
	exit 1
}

marker="RASDAEMON_CI_PROVISIONING_COMPLETE=$release"
grep -F "$marker" "$console" || {
	echo "guest provisioning marker is missing: $marker" >&2
	tail -n 100 "$console" >&2
	exit 1
}

mkdir -p "$(dirname "$output")"
qemu-img convert -O qcow2 -c "$work/custom.qcow2" "$output"
sha256sum "$output" >"$output.sha256"
printf '%s\n' "$base_url" >"$output.base-url"
printf '%s\n' "$expected" >"$output.base-sha512"

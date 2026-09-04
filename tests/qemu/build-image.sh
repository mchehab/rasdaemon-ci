#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

usage()
{
	cat <<EOF
Usage: $0 --arch ARCH --output IMAGE [--template NAME] [--source URI]

Builds a disposable rasdaemon guest with libguestfs virt-builder. This command
is explicit and may download the selected distribution template; normal unit
and QEMU test targets never invoke it automatically.
EOF
}

arch=
output=
template=
source_uri=${RAS_QEMU_VIRT_BUILDER_SOURCE:-}

while test $# -gt 0; do
	case $1 in
	--arch)
		arch=$2
		shift 2
		;;
	--output)
		output=$2
		shift 2
		;;
	--template)
		template=$2
		shift 2
		;;
	--source)
		source_uri=$2
		shift 2
		;;
	-h|--help)
		usage
		exit 0
		;;
	*)
		echo "Unknown option: $1" >&2
		usage >&2
		exit 2
		;;
	esac
done

test -n "$arch" || { echo "--arch is required" >&2; exit 2; }
test -n "$output" || { echo "--output is required" >&2; exit 2; }
command -v virt-builder >/dev/null || {
	echo "virt-builder is required (libguestfs-tools)" >&2
	exit 2
}

host_arch=$(uname -m)
case $host_arch in
amd64)
	host_arch=x86_64
	;;
arm64)
	host_arch=aarch64
	;;
esac
if test "$host_arch" != "$arch"; then
	echo "Image customization for $arch requires a native $arch host" >&2
	echo "virt-builder cannot run package installation for $arch on $host_arch" >&2
	exit 2
fi

case $arch in
x86_64)
	template=${template:-fedora-43}
	;;
aarch64)
	template=${template:-fedora-43}
	;;
riscv64)
	if test -z "$template"; then
		echo "No signed default riscv64 virt-builder template is available" >&2
		exit 2
	fi
	;;
*)
	echo "Unsupported architecture: $arch" >&2
	exit 2
	;;
esac

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
output=$(realpath -m "$output")
output_dir=$(dirname "$output")
mkdir -p "$output_dir"

case $template in
debian-*|ubuntu-*)
	packages="build-essential,meson,ninja-build,pkg-config,libtraceevent-dev,libsqlite3-dev,sqlite3,libpci-dev,python3,python3-sqlalchemy,util-linux,systemd"
	uefi_command=:
	;;
fedora-*|centos*|alma-*)
	packages="gcc,gcc-c++,make,meson,ninja-build,pkgconf-pkg-config,libtraceevent-devel,sqlite-devel,sqlite,pciutils-devel,python3,python3-sqlalchemy,util-linux,systemd"
	case $arch in
	x86_64)
		packages="$packages,grub2-efi-x64,grub2-efi-x64-modules"
		# Expanded by virt-builder inside the guest, not by this shell.
		# shellcheck disable=SC2016
		uefi_command='test -f /boot/efi/EFI/fedora/grubx64.efi && boot_uuid=$(awk '\''$2 == "/boot" { sub(/^UUID=/, "", $1); print $1 }'\'' /etc/fstab) && test -n "$boot_uuid" && mkdir -p /boot/efi/EFI/BOOT && cp -f /boot/efi/EFI/fedora/grubx64.efi /boot/efi/EFI/BOOT/BOOTX64.EFI && printf '\''search --no-floppy --fs-uuid --set=boot %s\nset prefix=($boot)/grub2\nconfigfile $prefix/grub.cfg\n'\'' "$boot_uuid" > /boot/efi/EFI/fedora/grub.cfg && cp -f /boot/efi/EFI/fedora/grub.cfg /boot/efi/EFI/BOOT/grub.cfg'
		;;
	aarch64)
		packages="$packages,grub2-efi-aa64,grub2-efi-aa64-modules"
		# Expanded by virt-builder inside the guest, not by this shell.
		# shellcheck disable=SC2016
		uefi_command='test -f /boot/efi/EFI/fedora/grubaa64.efi && boot_uuid=$(awk '\''$2 == "/boot" { sub(/^UUID=/, "", $1); print $1 }'\'' /etc/fstab) && test -n "$boot_uuid" && mkdir -p /boot/efi/EFI/BOOT && cp -f /boot/efi/EFI/fedora/grubaa64.efi /boot/efi/EFI/BOOT/BOOTAA64.EFI && printf '\''search --no-floppy --fs-uuid --set=boot %s\nset prefix=($boot)/grub2\nconfigfile $prefix/grub.cfg\n'\'' "$boot_uuid" > /boot/efi/EFI/fedora/grub.cfg && cp -f /boot/efi/EFI/fedora/grub.cfg /boot/efi/EFI/BOOT/grub.cfg'
		;;
	*)
		uefi_command=:
		;;
	esac
	;;
*)
	echo "No package map for template: $template" >&2
	exit 2
	;;
esac

set -- "$template" --arch "$arch" --format qcow2 --size 10G \
	--output "$output" --install "$packages" \
	--mkdir /usr/local/libexec \
	--mkdir /etc/systemd/system \
	--copy-in "$script_dir/guest/agent.py:/usr/local/libexec" \
	--copy-in "$script_dir/guest/ras-qemu-agent.service:/etc/systemd/system" \
	--run-command 'mv /usr/local/libexec/agent.py /usr/local/libexec/ras-qemu-agent' \
	--run-command 'chmod 0755 /usr/local/libexec/ras-qemu-agent' \
	--run-command "$uefi_command" \
	--run-command 'systemctl enable ras-qemu-agent.service' \
	--run-command 'systemctl mask apt-daily.service apt-daily-upgrade.service 2>/dev/null || true' \
	--run-command 'systemctl mask apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true' \
	--run-command "if test -f /etc/selinux/config; then sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config; fi"

if test -n "$source_uri"; then
	set -- --source "$source_uri" "$@"
fi

virt-builder "$@"

# The Fedora virt-builder x86_64 template is BIOS-oriented: it has a 1 MiB
# BIOS boot partition but no EFI System Partition.  OVMF cannot load the EFI
# binary from the XFS /boot filesystem, so add a small FAT ESP at the end of
# the expanded image and move the fallback loader there.
if test "$arch" = x86_64 && echo "$template" | grep -Eq '^(fedora-|centos|alma-)'; then
	command -v qemu-img >/dev/null || {
		echo "qemu-img is required to add the x86_64 EFI System Partition" >&2
		exit 2
	}
	command -v guestfish >/dev/null || {
		echo "guestfish is required to add the x86_64 EFI System Partition" >&2
		exit 2
	}
	qemu-img resize -q "$output" +256M
	guestfish --rw --format=qcow2 -a "$output" -- \
		run : \
		part-expand-gpt /dev/sda : \
		part-add /dev/sda p 20971520 -34 : \
		part-set-gpt-type /dev/sda 4 \
		c12a7328-f81f-11d2-ba4b-00a0c93ec93b : \
		mkfs vfat /dev/sda4 label:RAS-EFI : \
		mount /dev/sda3 / : \
		mount /dev/sda2 /boot : \
		mv /boot/efi /boot/efi.staged : \
		mkdir /boot/efi : \
		mount /dev/sda4 /boot/efi : \
		cp-a /boot/efi.staged/EFI /boot/efi : \
		rm-rf /boot/efi.staged
fi

sha256sum "$output" >"$output.sha256"
echo "Built $output"
echo "Record this digest in tests/qemu/images.json before publishing:"
cat "$output.sha256"

#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

repository=https://github.com/torvalds/linux.git
ref=
output=
jobs=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}

while test $# -gt 0; do
	case $1 in
	--repository) repository=$2; shift 2 ;;
	--ref) ref=$2; shift 2 ;;
	--output) output=$2; shift 2 ;;
	*) echo "Unknown argument: $1" >&2; exit 2 ;;
	esac
done

test -n "$ref" || { echo "--ref is required" >&2; exit 2; }
test -n "$output" || { echo "--output is required" >&2; exit 2; }
output=$(realpath -m "$output")

work=$(mktemp -d "${TMPDIR:-/tmp}/ras-kernel-build.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
git init -q "$work/source"
git -C "$work/source" remote add origin "$repository"
git -C "$work/source" fetch -q --depth=1 origin "$ref"
git -C "$work/source" checkout -q --detach FETCH_HEAD
commit=$(git -C "$work/source" rev-parse HEAD)

mkdir -p "$work/build" "$output/modules" "$output/metadata"
make -C "$work/source" O="$work/build" x86_64_defconfig
config="$work/source/scripts/config"
config_enable=()
config_enable+=(ACPI_APEI)
config_enable+=(ACPI_APEI_EINJ)
config_enable+=(ACPI_APEI_GHES)
config_enable+=(ACPI_APEI_MEMORY_FAILURE)
config_enable+=(ACPI_ERST)
config_enable+=(BLK_DEV_INITRD)
config_enable+=(CXL_MEM_RAW_COMMANDS)
config_enable+=(DEBUG_FS)
config_enable+=(DEBUG_INFO_NONE)
config_enable+=(DEVTMPFS)
config_enable+=(DEVTMPFS_MOUNT)
config_enable+=(EDAC)
config_enable+=(EXT4_FS)
config_enable+=(FTRACE)
config_enable+=(MEMORY_FAILURE)
config_enable+=(PCIEAER)
config_enable+=(PSTORE)
config_enable+=(PSTORE_BLK)
config_enable+=(RAS)
config_enable+=(SERIAL_8250)
config_enable+=(SERIAL_8250_CONSOLE)
config_enable+=(TRACEFS_FS)
config_enable+=(TRACEPOINTS)
config_enable+=(TRACING)
config_enable+=(VFAT_FS)
config_enable+=(VIRTIO)
config_enable+=(VIRTIO_BLK)
config_enable+=(VIRTIO_CONSOLE)
config_enable+=(VIRTIO_PCI)
config_enable+=(X86_MCE)
config_enable+=(X86_MCE_AMD)
config_enable+=(X86_MCE_INTEL)

config_module=()
config_module+=(CXL_BUS)
config_module+=(CXL_PCI)
config_module+=(HWPOISON_INJECT)

config_disable=()
config_disable+=(DEBUG_INFO)

for option in "${config_enable[@]}"; do
	"$config" --file "$work/build/.config" --enable "$option"
done
for option in "${config_module[@]}"; do
	"$config" --file "$work/build/.config" --module "$option"
done
for option in "${config_disable[@]}"; do
	"$config" --file "$work/build/.config" --disable "$option"
done
make -C "$work/source" O="$work/build" olddefconfig
make -C "$work/source" O="$work/build" -j"$jobs" bzImage modules
release=$(make -s -C "$work/source" O="$work/build" kernelrelease)
make -C "$work/source" O="$work/build" \
	modules_install INSTALL_MOD_PATH="$output/modules" INSTALL_MOD_STRIP=1

cp "$work/build/arch/x86/boot/bzImage" "$output/vmlinuz-$release"
cp "$work/build/.config" "$output/config-$release"
cp "$work/build/System.map" "$output/System.map-$release"
cat >"$output/metadata/kernel.json" <<EOF
{"repository":"$repository","ref":"$ref","commit":"$commit","release":"$release"}
EOF

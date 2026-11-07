#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

set -o pipefail

repository=https://github.com/torvalds/linux.git
ref=
arch=x86_64
output=
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
jobs=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}

while test $# -gt 0; do
	case $1 in
	--arch) arch=$2; shift 2 ;;
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
case $arch in
x86_64) kernel_arch=x86; defconfig=x86_64_defconfig; image_target=bzImage; image_path=arch/x86/boot/bzImage ;;
aarch64|arm64) arch=aarch64; kernel_arch=arm64; defconfig=defconfig; image_target=Image; image_path=arch/arm64/boot/Image ;;
*) echo "Unsupported architecture: $arch" >&2; exit 2 ;;
esac
export ARCH=$kernel_arch
settings=$(python3 "$script_dir/compilation.py" kernel "$arch")
defconfig=$(jq -r .defconfig <<< "$settings")
make -C "$work/source" O="$work/build" "$defconfig"
config="$work/source/scripts/config"
mapfile -t config_enable < <(jq -r '.enable[]' <<< "$settings")
mapfile -t config_module < <(jq -r '.module[]' <<< "$settings")
mapfile -t config_disable < <(jq -r '.disable[]' <<< "$settings")

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
# Kconfig can silently discard settings whose dependencies are absent.
# Optional test producers must not prevent building the bootable test kernel.
printf 'symbol\trequested\tactual\n' > "$output/metadata/config-status.tsv"

for option in ACPI_APEI_GHES ACPI_APEI_PCIEAER EDAC_GHES MEMORY_FAILURE \
	PCIEAER CXL_BUS CXL_PCI CXL_ACPI CXL_MEM CXL_REGION; do
	actual=$("$config" --file "$work/build/.config" --state "$option")

	if test "$actual" != y; then
		echo "::warning::Test prerequisite CONFIG_${option}=y is unavailable (actual: $actual)"
	fi

	printf 'CONFIG_%s\ty\t%s\n' "$option" "$actual" >> "$output/metadata/config-status.tsv"
done

make -C "$work/source" O="$work/build" -j"$jobs" "$image_target" modules
release=$(make -s -C "$work/source" O="$work/build" kernelrelease)
mapfile -t install_options < <(jq -r '.make[]' <<< "$settings")
make -C "$work/source" O="$work/build" \
	modules_install INSTALL_MOD_PATH="$output/modules" "${install_options[@]}"
mkdir -p "$work/test-driver"
cp "$script_dir/../guest/test-driver/Makefile" "$script_dir/../guest/test-driver/ras_ci.c" \
	"$work/test-driver/"
make -C "$work/source" O="$work/build" M="$work/test-driver" modules
make -C "$work/source" O="$work/build" M="$work/test-driver" \
	modules_install INSTALL_MOD_PATH="$output/modules"
test -d "$output/modules/lib/modules/$release"

cp "$work/build/$image_path" "$output/vmlinuz-$release"
cp "$work/build/.config" "$output/config-$release"
cp "$work/build/System.map" "$output/System.map-$release"
cat >"$output/metadata/kernel.json" <<EOF
{"repository":"$repository","ref":"$ref","commit":"$commit","release":"$release","arch":"$arch"}
EOF

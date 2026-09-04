#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only

set -eu

repository=https://github.com/torvalds/linux.git
ref=
output=
fragment=
jobs=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}

while test $# -gt 0; do
	case $1 in
	--repository) repository=$2; shift 2 ;;
	--ref) ref=$2; shift 2 ;;
	--fragment) fragment=$2; shift 2 ;;
	--output) output=$2; shift 2 ;;
	*) echo "Unknown argument: $1" >&2; exit 2 ;;
	esac
done

test -n "$ref" || { echo "--ref is required" >&2; exit 2; }
test -f "$fragment" || { echo "--fragment must name a file" >&2; exit 2; }
test -n "$output" || { echo "--output is required" >&2; exit 2; }

work=$(mktemp -d "${TMPDIR:-/tmp}/ras-kernel-build.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
git init -q "$work/source"
git -C "$work/source" remote add origin "$repository"
git -C "$work/source" fetch -q --depth=1 origin "$ref"
git -C "$work/source" checkout -q --detach FETCH_HEAD
commit=$(git -C "$work/source" rev-parse HEAD)

mkdir -p "$work/build" "$output/modules" "$output/metadata"
make -C "$work/source" O="$work/build" x86_64_defconfig
KCONFIG_CONFIG="$work/build/.config" \
	"$work/source/scripts/kconfig/merge_config.sh" -m -O "$work/build" \
	"$work/build/.config" "$fragment"
make -C "$work/source" O="$work/build" olddefconfig
# Treat dropped or renamed requirements as a failed image recipe. This keeps
# a new upstream kernel from silently producing less test coverage.
sed -n 's/^\(CONFIG_[A-Za-z0-9_]*\)=.*/\1/p' "$fragment" > \
	"$work/required-options"
while IFS= read -r option; do
	grep -q "^$option=" "$work/build/.config" || {
		echo "Required kernel option was not enabled: $option" >&2
		exit 1
	}
done < "$work/required-options"
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

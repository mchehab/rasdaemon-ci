#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

repository=https://gitlab.com/mchehab_kernel/qemu.git
ref=rasdaemon_tests
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

test -n "$output" || { echo "--output is required" >&2; exit 2; }
output=$(realpath -m "$output")
work=$(mktemp -d "${TMPDIR:-/tmp}/ras-qemu-build.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM

git init -q "$work/source"
git -C "$work/source" remote add origin "$repository"
git -C "$work/source" fetch -q --depth=1 origin "$ref"
git -C "$work/source" checkout -q --detach FETCH_HEAD
commit=$(git -C "$work/source" rev-parse HEAD)

mkdir -p "$work/build" "$output/root" "$output/metadata"
cd "$work/build"
"$work/source/configure" \
	--target-list=x86_64-softmmu \
	--prefix=/opt/qemu \
	--disable-docs \
	--disable-werror \
	--enable-strip
make -j"$jobs"
make install DESTDIR="$output/root"

# These scripts are part of the test interface but are not installed by QEMU.
mkdir -p "$output/root/opt/qemu/libexec/rasdaemon"
for script in ghes_inject.py arm_processor_error.py qmp_helper.py; do
	cp "$work/source/scripts/$script" \
		"$output/root/opt/qemu/libexec/rasdaemon/$script"
done

rm -rf "$output/root/opt/qemu/share/doc" \
	"$output/root/opt/qemu/share/man"
cat >"$output/metadata/qemu.json" <<EOF
{"repository":"$repository","ref":"$ref","commit":"$commit"}
EOF

"$output/root/opt/qemu/bin/qemu-system-x86_64" --version
test -x "$output/root/opt/qemu/bin/qemu-img"

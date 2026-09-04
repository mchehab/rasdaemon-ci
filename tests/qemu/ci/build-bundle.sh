#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

channel=release
kernel_repository=https://github.com/torvalds/linux.git
kernel_ref=
qemu_repository=https://gitlab.com/mchehab_kernel/qemu.git
qemu_ref=rasdaemon_tests
output=build/rasdaemon-ci
tag=rasdaemon-ci:local
base_sha512=
qemu_dir=
kernel_dir=
kernel_json=
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(realpath "$script_dir/../../..")

while test $# -gt 0; do
	case $1 in
	--channel) channel=$2; shift 2 ;;
	--kernel-repository) kernel_repository=$2; shift 2 ;;
	--kernel-ref) kernel_ref=$2; shift 2 ;;
	--qemu-repository) qemu_repository=$2; shift 2 ;;
	--qemu-ref) qemu_ref=$2; shift 2 ;;
	--output) output=$2; shift 2 ;;
	--tag) tag=$2; shift 2 ;;
	--base-sha512) base_sha512=$2; shift 2 ;;
	--qemu-dir) qemu_dir=$2; shift 2 ;;
	--kernel-dir) kernel_dir=$2; shift 2 ;;
	--kernel-json) kernel_json=$2; shift 2 ;;
	*) echo "Unknown argument: $1" >&2; exit 2 ;;
	esac
done

mkdir -p "$output"
output=$(realpath "$output")
if test -n "$kernel_json"; then
	kernel_json=$(cat "$kernel_json")
elif test -n "$kernel_ref"; then
	kernel_json=$(python3 "$project_dir/tests/qemu/source_refs.py" \
		resolve-kernel --repository "$kernel_repository" \
		--channel "$channel" --ref "$kernel_ref")
else
	kernel_json=$(python3 "$project_dir/tests/qemu/source_refs.py" \
		resolve-kernel --repository "$kernel_repository" \
		--channel "$channel")
fi
resolved_kernel_ref=$(printf '%s' "$kernel_json" | \
	python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')

test -n "$qemu_dir" || qemu_dir="$output/qemu"
test -n "$kernel_dir" || kernel_dir="$output/kernel"
qemu_dir=$(realpath -m "$qemu_dir")
kernel_dir=$(realpath -m "$kernel_dir")
context="$output/context"
rm -rf "$context"
mkdir -p "$context/rasdaemon-ci/kernel"
mkdir -p "$context/rasdaemon-ci/harness/guest"

if test ! -f "$qemu_dir/metadata/qemu.json"; then
	"$script_dir/build-qemu.sh" --repository "$qemu_repository" \
		--ref "$qemu_ref" --output "$qemu_dir"
fi
if test ! -f "$kernel_dir/metadata/kernel.json"; then
	"$script_dir/build-kernel.sh" --repository "$kernel_repository" \
		--ref "$resolved_kernel_ref" \
		--output "$kernel_dir"
fi
if test -n "$base_sha512"; then
	"$script_dir/build-guest.sh" --kernel "$kernel_dir" \
		--base-sha512 "$base_sha512" \
		--output "$context/rasdaemon-ci/rasdaemon-guest-x86_64.qcow2"
else
	"$script_dir/build-guest.sh" --kernel "$kernel_dir" \
		--output "$context/rasdaemon-ci/rasdaemon-guest-x86_64.qcow2"
fi

cp -a "$qemu_dir/root" "$context/root"
cp "$kernel_dir/metadata/kernel.json" "$context/rasdaemon-ci/kernel/"
cp "$kernel_dir/config-"* "$context/rasdaemon-ci/kernel/"
cp "$qemu_dir/metadata/qemu.json" "$context/rasdaemon-ci/"
cp "$project_dir/tests/qemu/oci/rasdaemon-ci.py" "$context/"
cp "$project_dir/tests/qemu/ras_qemu.py" \
	"$context/rasdaemon-ci/harness/"
cp "$project_dir/tests/qemu/guest/agent.py" \
	"$context/rasdaemon-ci/harness/guest/"
image_sha=$(sha256sum "$context/rasdaemon-ci/rasdaemon-guest-x86_64.qcow2" | awk '{print $1}')
cat >"$context/rasdaemon-ci/images.json" <<EOF
{
  "format_version": 1,
  "architectures": {
    "x86_64": {
      "qemu_binary": "qemu-system-x86_64",
      "machine": "q35",
      "cpu": {"kvm": "host", "tcg": "max"},
      "memory_mb": 2048,
      "cpus": 2,
      "firmware": {"pairs": [{
        "code": "/opt/qemu/share/qemu/edk2-x86_64-code.fd",
        "vars": "/opt/qemu/share/qemu/edk2-i386-vars.fd"
      }]},
      "image": {
        "filename": "rasdaemon-guest-x86_64.qcow2",
        "sha256": "$image_sha",
        "source": "oci:$tag"
      }
    }
  },
  "kernel": $kernel_json
}
EOF
cp "$project_dir/tests/qemu/oci/Containerfile" "$context/Containerfile"

docker build --file "$context/Containerfile" --tag "$tag" "$context"

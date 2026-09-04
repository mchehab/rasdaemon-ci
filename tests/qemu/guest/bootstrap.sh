#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only

set -eu

mount_dir=/run/rasdaemon-payload
mkdir -p "$mount_dir"

deadline=60
while test "$deadline" -gt 0; do
	for device in $(lsblk -nrpo NAME,FSTYPE | awk '$2 ~ /^(vfat|fat|msdos)$/ {print $1}'); do
		if mount -o ro "$device" "$mount_dir" 2>/dev/null; then
			if test -f "$mount_dir/agent.py"; then
				exec /usr/bin/python3 "$mount_dir/agent.py"
			fi
			umount "$mount_dir"
		fi
	done
	sleep 1
	deadline=$((deadline - 1))
done

echo "ras-qemu-bootstrap: payload was not found" >&2
exit 1

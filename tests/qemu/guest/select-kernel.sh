#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

set -o pipefail

release=${1:?kernel release is required}
config=/etc/default/grub.d/99-rasdaemon.cfg
grub_cfg=/boot/grub/grub.cfg
grub_env=/boot/grub/grubenv
entry_prefix="gnulinux-$release-advanced-"

test -f "/boot/vmlinuz-$release"
test -f "/boot/initrd.img-$release"

printf 'GRUB_DEFAULT=saved\n' >"$config"
# This is a disposable emulated platform, outside EDAC's physical-platform
# allowlist. Enable the genuine GHES-to-EDAC notifier for memory CPER tests.
# shellcheck disable=SC2016 # Expand GRUB_CMDLINE_LINUX when grub sources this file.
printf 'GRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX ghes.edac_force_enable=1"\n' >>"$config"
update-grub

test -f "$grub_cfg"
grep -F "vmlinuz-$release" "$grub_cfg" >/dev/null

kernel_entries=$(awk -F"'" -v prefix="$entry_prefix" '
	/^[[:space:]]*menuentry / {
		for (i = 2; i <= NF; i += 2) {
			if (index($i, prefix) == 1) {
				print $i
			}
		}
	}' "$grub_cfg")
test "$(printf '%s\n' "$kernel_entries" | grep -c .)" -eq 1
kernel_entry=$kernel_entries

suffix=${kernel_entry#"$entry_prefix"}
test -n "$suffix"
test "$suffix" != "$kernel_entry"

submenu="gnulinux-advanced-$suffix"
submenu_count=$(awk -F"'" -v expected="$submenu" '
	/^[[:space:]]*submenu / {
		for (i = 2; i <= NF; i += 2) {
			if ($i == expected) {
				count++
			}
		}
	}
	END { print count + 0 }' "$grub_cfg")
test "$submenu_count" -eq 1

entry="$submenu>$kernel_entry"

grub-set-default "$entry"
saved=$(grub-editenv "$grub_env" list |
	awk -F= '$1 == "saved_entry" {print substr($0, length($1) + 2)}')
test "$saved" = "$entry"
printf 'selected GRUB entry: %s\n' "$entry"

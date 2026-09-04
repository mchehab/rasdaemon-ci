#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

set -o pipefail

release=${1:?kernel release is required}
payload=/mnt/payload

test -f "$payload/vmlinuz-$release"
test -f "$payload/config-$release"
test -f "$payload/System.map-$release"
test -d "$payload/modules/lib/modules/$release"

install -D -m 0644 "$payload/vmlinuz-$release" \
	"/boot/vmlinuz-$release"
install -D -m 0644 "$payload/config-$release" \
	"/boot/config-$release"
install -D -m 0644 "$payload/System.map-$release" \
	"/boot/System.map-$release"
cp -a "$payload/modules/lib/modules/$release" /lib/modules/
install -D -m 0755 "$payload/guest/bootstrap.sh" \
	/usr/local/libexec/bootstrap.sh
install -D -m 0644 "$payload/guest/ras-qemu-agent.service" \
	/etc/systemd/system/ras-qemu-agent.service
install -D -m 0755 "$payload/guest/select-kernel.sh" \
	/usr/local/libexec/select-kernel.sh
ln -sf bootstrap.sh /usr/local/libexec/ras-qemu-agent

depmod "$release"
update-initramfs -c -k "$release"
/usr/local/libexec/select-kernel.sh "$release"
systemctl enable ras-qemu-agent.service
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target \
	suspend-then-hibernate.target
mkdir -p /etc/systemd/logind.conf.d
printf '[Login]\nIdleAction=ignore\n' > /etc/systemd/logind.conf.d/ras-tests.conf
systemctl mask apt-daily.service apt-daily-upgrade.service apt-daily.timer \
	apt-daily-upgrade.timer || true
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

printf 'RASDAEMON_CI_PROVISIONING_COMPLETE=%s\n' "$release"

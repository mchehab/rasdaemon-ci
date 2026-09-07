// SPDX-License-Identifier: GPL-2.0-only
/* Deterministic software producers for disposable QEMU functional-test guests. */
#include <linux/debugfs.h>
#include <linux/etherdevice.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/uaccess.h>
#include <ras/ras_event.h>

static struct net_device *test_netdev;
static struct dentry *test_debugfs;

static int ras_ci_open(struct net_device *dev)
{
	netif_carrier_on(dev);
	netif_start_queue(dev);
	return 0;
}

static int ras_ci_stop(struct net_device *dev)
{
	netif_stop_queue(dev);
	return 0;
}

static netdev_tx_t ras_ci_xmit(struct sk_buff *skb, struct net_device *dev)
{
	/* A deliberately stalled queue lets the real networking watchdog fire. */
	netif_stop_queue(dev);
	dev_kfree_skb(skb);
	return NETDEV_TX_OK;
}

static void ras_ci_timeout(struct net_device *dev, unsigned int queue)
{
	netif_wake_queue(dev);
}

static const struct net_device_ops ras_ci_netdev_ops = {
	.ndo_open = ras_ci_open,
	.ndo_stop = ras_ci_stop,
	.ndo_start_xmit = ras_ci_xmit,
	.ndo_tx_timeout = ras_ci_timeout,
};

static ssize_t ras_ci_extlog(struct file *file, const char __user *buf,
			     size_t count, loff_t *pos)
{
#if IS_ENABLED(CONFIG_ACPI_EXTLOG)
	struct cper_sec_mem_err memory = {
		.validation_bits = CPER_MEM_VALID_PA | CPER_MEM_VALID_ERROR_TYPE,
		.physical_addr = 0x12345000,
		.error_type = 2,
	};
	const guid_t fru = GUID_INIT(0x12345678, 0x1234, 0x5678,
				     0x9a, 0xbc, 0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc);
	char value;

	if (count != 1 || get_user(value, buf) || value != '1')
		return -EINVAL;
	/* This only emits a trace record; it does not touch the specified address. */
	trace_extlog_mem_event(&memory, 17, &fru, "rasdaemon-ci-extlog", CPER_SEV_CORRECTED);
	return count;
#else
	return -EOPNOTSUPP;
#endif
}

static const struct file_operations ras_ci_extlog_ops = {
	.owner = THIS_MODULE,
	.write = ras_ci_extlog,
	.llseek = noop_llseek,
};

static int __init ras_ci_init(void)
{
	int ret;

	test_netdev = alloc_etherdev(0);
	if (!test_netdev)
		return -ENOMEM;
	strscpy(test_netdev->name, "rasci%d", IFNAMSIZ);
	test_netdev->netdev_ops = &ras_ci_netdev_ops;
	test_netdev->watchdog_timeo = HZ;
	eth_hw_addr_random(test_netdev);
	ret = register_netdev(test_netdev);
	if (ret) {
		free_netdev(test_netdev);
		return ret;
	}
	test_debugfs = debugfs_create_dir("ras_ci", NULL);
	debugfs_create_file("extlog", 0200, test_debugfs, NULL, &ras_ci_extlog_ops);
	return 0;
}

static void __exit ras_ci_exit(void)
{
	debugfs_remove_recursive(test_debugfs);
	unregister_netdev(test_netdev);
	free_netdev(test_netdev);
}

module_init(ras_ci_init);
module_exit(ras_ci_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Software event producers for rasdaemon QEMU tests");

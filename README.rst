rasdaemon-ci
============

Independent continuous-integration infrastructure for `rasdaemon
<https://github.com/mchehab/rasdaemon>`_. It builds current Linux and QEMU
sources, exercises rasdaemon in isolated virtual machines, and publishes
results intended for human review.

Latest reports
--------------

.. |feature-pass| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-feature-pass.svg
   :alt: Features passed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |feature-fail| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-feature-fail.svg
   :alt: Features failed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |kernel-pass| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-kernel-pass.svg
   :alt: Kernel checks passed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |kernel-fail| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-kernel-fail.svg
   :alt: Kernel checks failed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |rasdaemon-pass| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-rasdaemon-pass.svg
   :alt: rasdaemon checks passed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |rasdaemon-fail| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-rasdaemon-fail.svg
   :alt: rasdaemon checks failed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |x86-vm| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-x86-vm-fail.svg
   :alt: x86_64 VM status
   :target: https://mchehab.github.io/rasdaemon-ci/daily/x86_64/
.. |arm64-vm| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-arm64-vm-fail.svg
   :alt: ARM64 VM status
   :target: https://mchehab.github.io/rasdaemon-ci/daily/aarch64/

.. list-table:: Daily functional results
   :header-rows: 1

   * - Scope
     - PASS
     - FAIL
   * - Features
     - |feature-pass|
     - |feature-fail|
   * - Kernel checks
     - |kernel-pass|
     - |kernel-fail|
   * - rasdaemon checks
     - |rasdaemon-pass|
     - |rasdaemon-fail|

.. list-table:: VM health
   :header-rows: 1

   * - Architecture
     - Status
   * - x86_64
     - |x86-vm|
   * - ARM64
     - |arm64-vm|

* `Daily functional-test report
  <https://mchehab.github.io/rasdaemon-ci/daily/>`_

Functional feature implementation
---------------------------------

The scope of RAS Daemon functional tests CI is to use QEMU virtual machines
together with error injection code to verify that each functionality is
working.

The tables below show the scope of the RAS Daemon functional tests compared
with running it on real hardware. There are three types of
feature check support:

- **Implemented**

  There is an event error injection or a consumer check capable of
  measuring that the functionality is working as expected;

- **Partial**

  Indicates that there is such mechanisms but it covers only part of
  the scope; either because this is enough to check the functionality or
  because part of the check can't be done with the current emulation
  support;

- **Not implemented**

  Indicates that it is either not possible currently to test the feature
  or that testing it is out of the current scope.

The daily HTML report first shows a feature-status summary, followed by run
health for the x86_64 and aarch64 VMs. Its feature-coverage and ``Features not
tested`` tables provide the individual checks and reasons for N/A results.
N/A features and unavailable portions are excluded from PASS/FAIL regression
totals; they do not indicate a failed test.

.. list-table:: Implemented Feature tests
   :header-rows: 1
   :widths: 22 12 66

   * - Feature
     - Owner
     - Implemented check
   * - abrt-report
     - aarch64
     - Receive and inspect the ABRT socket report for an injected memory failure.
   * - aer
     - x86_64
     - Native PCIe and firmware-first GHES delivery, matching recorded AER data.
   * - ampere-oem-sel
     - aarch64
     - Separate consumer assertion on the emulated BMC's type 0xc0 SEL record.
   * - arm
     - aarch64
     - GHES ARM processor CPER and matching processor identification.
   * - arm-vendor-data (internal)
     - aarch64
     - ARM CPER vendor bytes retained in the database and decoded in output.
   * - bmc-generic
     - x86_64
     - Opt-in generic type 0x02 SEL consumer; verify BDF and severity bytes.
   * - cpu-fault-isolation
     - aarch64
     - Verify CPU 1 is offlined after the ARM event; restore its initial state.
   * - db (internal)
     - aarch64
     - Installed database-report CLI must return the injected record's fields.
   * - devlink
     - aarch64
     - netdevsim health report and a software device's real TX watchdog timeout;
       these are software/device-model producers, not physical hardware faults.
   * - diskerror
     - aarch64
     - QEMU blkdebug I/O failure and matching block error record.
   * - ipmi-bmc (internal)
     - x86_64
     - Shared IPMI transport exercised by the generic BMC consumer assertion.
   * - mc-event (always available)
     - aarch64
     - GHES memory CPER and recorded memory-controller event.
   * - mce
     - x86_64
     - Hardware-first machine-check injection and decoded event assertions.
   * - memory-ce-pfa
     - aarch64
     - Corrected-error threshold and successful isolation of a disposable page.
   * - memory-failure
     - aarch64
     - Poison a disposable helper's page and match the reported PFN.
   * - memory-row-ce-pfa
     - aarch64
     - Row threshold and isolation of the disposable page identified by CPER.
   * - mysql
     - x86_64
     - Real MariaDB service, recording backend and installed CLI readback.
   * - non-standard (internal)
     - aarch64
     - PCI bus and unknown-GUID CPER injections require exact GUID and payload
       bytes. PCI bus delivery also exercises explicit zero-valued fields.
   * - openbmc-unified-sel
     - aarch64
     - Separate assertion on the Facebook/Meta type 0xfb SEL consumer record.
   * - page-isolation (internal)
     - aarch64
     - Shared isolation mechanism checked by page and row PFA consumers.
   * - poison-page-stat (internal)
     - aarch64
     - Memory-failure injection must produce the configured threshold warning.
   * - postgresql
     - x86_64
     - Real PostgreSQL service, recording backend and installed CLI readback.
   * - signal
     - aarch64
     - A disposable process accesses its poisoned page to provoke SIGBUS.
   * - sqlite3
     - aarch64
     - Separate assertion that the injected memory-failure row was committed.
   * - trigger (internal)
     - aarch64
     - Execute a real trigger and check its PFN and action-result environment.

.. list-table:: Partial Support
   :header-rows: 1
   :widths: 22 12 66

   * - Feature
     - Owner
     - Implemented check / remaining work
   * - amp-ns-decode
     - aarch64
     - Ampere payload 0 fixture and decoded fields; other payload formats remain.
   * - cxl
     - x86_64
     - Media, DRAM, module, poison, overflow, AER and memory-sparing checks
       exist. Sparing uses a real mailbox resource query for an informational
       event. A generic event producer remains to be implemented and is listed
       separately as N/A.
   * - erst
     - x86_64
     - Load the x86 MCE ERST submodule, write/read a real ERST CPER, consume
       both pstore layouts with legacy-first selection, and verify deletion
       survives remount. Fatal-MCE reboot and post-boot discovery are not
       simulated.
   * - extlog
     - x86_64
     - Guest software producer emits the kernel EXTLOG tracepoint; verify
       decoded fields. Physical firmware EXTLOG delivery is not simulated.
   * - hisi-ns-decode
     - aarch64
     - All four registered formats: common, HIP08 OEM type 1, OEM type 2,
       and PCIe local. Batched fixtures cover every named module/submodule,
       known and unknown severity, each validity bit, BDF and register dumps.
       Execution and malformed-input coverage are not yet simulated.
   * - jaguar-ns-decode
     - aarch64
     - Jaguar payload 0 fixture; additional payload layouts remain untested.
   * - nvidia-ns-decode
     - aarch64
     - NVIDIA non-standard fixture and decoded fields; broader format coverage
       remains to be audited.
   * - yitian-ns-decode
     - aarch64
     - Yitian DDR fixture and register dump; other event types remain untested.

.. list-table:: Not implemented
   :header-rows: 1
   :widths: 22 12 66

   * - Feature
     - Owner
     - Reason
   * - debug-sql (internal)
     - aarch64
     - No functional SQL debug-logging test is assigned.
   * - pcie-edpc
     - x86_64
     - Needs a DPC-capable emulated port and assertions on its control register;
       the current QEMU test topology does not provide that capability.
   * - cxl / cxl-generic
     - x86_64
     - The harness has no producer for a generic CXL event. The other CXL
       event families remain covered by the Partial CXL checks.
   * - reri
     - riscv64
     - QEMU currently has no RERI event producer or injection mechanism. The
       feature remains a planned RISC-V test once QEMU can simulate it.

QEMU test model
---------------

The injection profile exercises the real guest kernel, rasdaemon trace
consumers, SQLite recording, and ``ras-mc-ctl`` reports. The scenarios are
defined in ``tests/qemu/scenarios.json`` and include native MCE/AER, block-I/O
errors, GHES/CPER, CXL mailbox and QMP events, guest hwpoison, SIGBUS, software
EXTLOG, netdevsim health reports, and vendor decoder fixtures. The x86 VM can
therefore exercise ARM CPER decoding, but this does not represent ARM CPU
hardware.

Each scenario waits for its required tracepoint and guest readiness before
injection, then waits for the matching committed database row. Results retain
decoded database data, daemon output, producer commands or QMP transcripts,
and host/guest diagnostics. Missing drivers, disabled tracepoints, skipped
required scenarios, and failed assertions are failures; unavailable producers
are recorded as N/A.

The kernel configuration requires GHES-to-EDAC, GHES PCIe AER, CXL, PCI, ACPI,
memory and region support, with ``HWPOISON_INJECT=m`` for disposable-page
tests. The guest uses ``ghes.edac_force_enable=1`` because QEMU is outside the
physical-platform allowlist, and the payload enables all rasdaemon
architectures so CPER consumers can be exercised on the x86 guest.

Additional notes regarding partial and not implemented scenarios
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

ERST scope
~~~~~~~~~~

On current x86 Linux, the MCE core serializes a machine check as a CPER record
and writes it to firmware ERST storage. At a subsequent pstore scan (normally
on boot or when pstore is mounted), the ERST pstore backend exposes that record
as ``/sys/fs/pstore/mce-erst-<record-id>``. The kernel creates records at the
pstore mount root; it does not create ``/sys/fs/pstore/erst`` itself.

The ERST reader is an x86 MCE submodule. It first checks for an accessible
historical ``/sys/fs/pstore/erst`` mount, then falls back to the current flat
``/sys/fs/pstore`` mount. This order matters: the legacy mount can coexist
with an accessible parent directory, which otherwise would hide its records.
The two locations do not need to exist together. The functional test uses
``erst_dbg`` to write a CPER MCE record, then mounts pstore in each supported
layout to exercise real persistence, module initialization, discovery, decode,
and deletion. It also proves the precedence rule by leaving a record at the
flat root while the legacy directory is present: the record is decoded only
when the flat layout is selected.
It intentionally does not inject a fatal MCE and reboot the VM; that additional
MCE-core-to-ERST lifecycle test remains future work.

RERI limitation
~~~~~~~~~~~~~~~

A RISC-V CPU emulation alone does not supply a RERI error source. QEMU
currently has no RERI event producer or injection mechanism. An
`experimental RAS stack
<https://lists.infradead.org/pipermail/linux-riscv/2026-January/083224.html>`_
documents RERI emulation using QEMU together with OpenSBI, EDK2 and Linux
changes. That stack delivers errors through GHES/CPER; rasdaemon's ``reri``
decoder expects the separate ``ras:reri_event`` tracepoint. Their connection
would have to be verified or implemented before claiming RERI decoder coverage.
The inspected local QEMU and Linux trees contain no RERI producer.
Once QEMU exposes a producer, a RISC-V firmware, kernel and guest scenario
can be added.

ARM reference platform
----------------------

As ARM servers come with several flavors with different vendor features, the
CI engine was set to emulate a Huawei TaiShan 2280 V2, board BC82AMDD, with
two 64-core Kunpeng-920 processors and four NUMA nodes. Such servers have a
firmware that exposes HEST, EINJ, ERST and BERT, among other ACPI tables.
No serial numbers, UUIDs, or raw host ACPI tables are used as test fixtures.

The aarch64 VM remains QEMU's ``virt`` platform, with native host CPU support
when KVM is usable and an emulated CPU. It is not a full emulation of Kunpeng's
physical peripherals or memory map. HiSilicon non-standard decoding is selected
by CPER section GUID, not SMBIOS model, so the complete valid-format corpus
does not require emulating a TaiShan firmware or changing QEMU source.

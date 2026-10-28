+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| Feature     | PASS | FAIL | N/A | Reason                                                                                |
+=============+======+======+=====+=======================================================================================+
| aer         | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| bmc-generic | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| cxl         | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| erst        | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| extlog      | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| ipmi-bmc    | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| mce         | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| mysql       | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+
| postgresql  | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+-------------+------+------+-----+---------------------------------------------------------------------------------------+

Features not tested
-------------------

+-----------+--------------+----------------------------+-----------------------------------------------------------------------------------+
| Feature   | Architecture | Missing check              | Reason                                                                            |
+===========+==============+============================+===================================================================================+
| cxl       | x86_64       | cxl-generic                | No generic CXL event producer has been implemented in the harness                 |
+-----------+--------------+----------------------------+-----------------------------------------------------------------------------------+
| erst      | x86_64       | erst-additional-coverage   | A fatal MCE followed by reboot and post-boot ERST discovery is not simulated      |
+-----------+--------------+----------------------------+-----------------------------------------------------------------------------------+
| extlog    | x86_64       | extlog-additional-coverage | The producer is software-only; physical firmware EXTLOG delivery is not simulated |
+-----------+--------------+----------------------------+-----------------------------------------------------------------------------------+
| pcie-edpc | x86_64       | pcie-edpc                  | The current QEMU test topology has no DPC-capable PCIe port                       |
+-----------+--------------+----------------------------+-----------------------------------------------------------------------------------+

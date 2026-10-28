+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| Feature             | PASS | FAIL | N/A | Reason                                                                                |
+=====================+======+======+=====+=======================================================================================+
| abrt-report         | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| amp-ns-decode       | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| ampere-oem-sel      | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| arm                 | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| arm-vendor-data     | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| cpu-fault-isolation | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| db                  | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| devlink             | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| diskerror           | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| hisi-ns-decode      | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| jaguar-ns-decode    | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| mc-event            | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| memory-ce-pfa       | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| memory-failure      | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| memory-row-ce-pfa   | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| non-standard        | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| nvidia-ns-decode    | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| openbmc-unified-sel | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| page-isolation      | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| poison-page-stat    | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| signal              | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| sqlite3             | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| trigger             | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+
| yitian-ns-decode    | 0    | 0    | 1   | Not run due to infrastructure failure: no matching pair found; no matching pair found |
+---------------------+------+------+-----+---------------------------------------------------------------------------------------+

Features not tested
-------------------

+------------------+--------------+--------------------------------------+--------------------------------------------------------------------------------------------+
| Feature          | Architecture | Missing check                        | Reason                                                                                     |
+==================+==============+======================================+============================================================================================+
| amp-ns-decode    | aarch64      | amp-ns-decode-additional-coverage    | Only the Ampere payload-0 format has a producer; other AMP payload formats remain untested |
+------------------+--------------+--------------------------------------+--------------------------------------------------------------------------------------------+
| debug-sql        | aarch64      | debug-sql-additional-coverage        | No functional SQL debug-logging test or producer is assigned                               |
+------------------+--------------+--------------------------------------+--------------------------------------------------------------------------------------------+
| hisi-ns-decode   | aarch64      | hisi-ns-decode-additional-coverage   | Malformed input and execution through every registered format remain untested              |
+------------------+--------------+--------------------------------------+--------------------------------------------------------------------------------------------+
| jaguar-ns-decode | aarch64      | jaguar-ns-decode-additional-coverage | Only Jaguar payload-0 is produced; additional payload layouts remain untested              |
+------------------+--------------+--------------------------------------+--------------------------------------------------------------------------------------------+
| nvidia-ns-decode | aarch64      | nvidia-ns-decode-additional-coverage | Only one NVIDIA non-standard format is produced; broader format coverage remains untested  |
+------------------+--------------+--------------------------------------+--------------------------------------------------------------------------------------------+
| yitian-ns-decode | aarch64      | yitian-ns-decode-additional-coverage | Only the Yitian DDR register-dump format is produced; other event types remain untested    |
+------------------+--------------+--------------------------------------+--------------------------------------------------------------------------------------------+

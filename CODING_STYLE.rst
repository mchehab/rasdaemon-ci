Coding style
============

This project favors code that is easy to scan while following a
long-running CI job. The rules below apply to Python, shell, and
workflow changes.

Python
------

-  Support Python 3.10 or newer.
-  Do not use ``pathlib``; use ``os``, ``os.path``, and explicit string
   paths instead.
-  Group related code into classes when it owns state or represents a
   distinct subsystem. Keep top-level functions for small stateless
   helpers and entry points.
-  Use type annotations for function parameters, return values, and
   important state. Prefer built-in generic types such as ``list[str]``
   and ``dict[str, str]`` available in Python 3.10.
-  Keep logical operations in separate visual groups with a blank line
   between them. For example, separate path construction, I/O, parsing,
   state changes, and returns.
-  Treat ``for``, ``while``, and ``match`` blocks as separate blocks.
   Leave a blank line before the block when it follows another operation
   and after it when subsequent work starts. Separate nested loops from
   the surrounding phase.
-  Do not put a function call's opening parenthesis at the end of a
   line. Keep calls on one line where practical. If a call needs
   multiple lines, construct data first (using ``[]`` or ``{}`` when
   appropriate), then pass that data to a call on a line that contains
   its opening parenthesis and arguments.
-  Multiline list and dictionary initializers may end a line with ``[``
   or ``{``.
-  Keep one statement per line.
-  Put blank lines between different phases of a function and between
   related groups of class methods.
-  Prefer descriptive local variables over deeply nested expressions.
-  Preserve existing behavior when applying style-only changes.

Shell and workflows
-------------------

-  Put stable shell options in the shebang when practical (for example,
   ``#!/bin/bash -eux``). Enable ``pipefail`` during initialization.
-  Always enable ``-x`` tracing. It may be enabled after top-level function
   definitions when that avoids tracing definitions that will run later, but
   it must be enabled before the operational phases begin.
-  Keep independent command phases separated by blank lines: argument
   parsing, validation, setup, execution, packaging, and cleanup should
   be visually distinct.
-  Treat ``for``, ``while``, and ``case`` blocks as separate blocks,
   with blank lines before and after them when another phase surrounds
   the block.
-  Keep one logical command or operation per line where possible. Use
   arrays for command arguments and variables for paths, URLs, and
   temporary files.
-  Put blank lines between a loop or conditional branch and the next
   operation.
-  Use functions for repeated shell behavior, with the function name and
   opening brace on separate lines when that improves readability.
-  Keep heredoc payloads and embedded scripts visually isolated from the
   shell operations that create and consume them.

Validation
----------

Before committing a change, run the focused tests and at least:

.. code:: sh

   python3 -m py_compile <changed-python-files>
   pylint <changed-python-files>
   git diff --check

Pylint is a required development tool. If it is not installed on the current
machine, report that explicitly instead of silently skipping the check. Keep
all Pylint message classes enabled during review.
Executable Python scripts with a ``__main__`` entry point may use a hyphenated
filename; suppress only the module-name diagnostic for those files. Keep other
Pylint naming diagnostics enabled. Any intentional exception must use a
localized ``pylint: disable=<numeric-id>`` comment. Use numeric Pylint message
IDs rather than symbolic names.

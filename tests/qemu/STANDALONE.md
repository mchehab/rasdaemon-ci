# Standalone rasdaemon CI repository

The standalone repository is an intermediate home for the QEMU functional
test harness.  Its scripts accept a rasdaemon checkout as an input and do not
depend on rasdaemon's Git history, so they can later move back into the main
repository.

## Create the repository

Keep this prototype checkout unchanged until the migration is verified.  Make
an empty public `mchehab/rasdaemon-ci` repository without an initial README,
license, or `.gitignore`, then clone it beside this checkout:

```sh
gh repo create mchehab/rasdaemon-ci --public \
    --description "QEMU functional CI for rasdaemon" --disable-wiki
git clone https://github.com/mchehab/rasdaemon-ci.git \
    ../rasdaemon-ci-standalone
```

If `gh` is unavailable, create the empty repository in the GitHub web UI and
run only the `git clone` command.  Do not repoint this prototype's `origin`.

Seed the new repository with `COPYING`, `rasdaemon.lock.json`, the QEMU harness
and tests, and a harness-only Makefile.  Do not copy the rasdaemon source tree
or its unrelated workflows.  `tests/qemu/export-standalone.sh` creates this
allowlisted tree without modifying either Git repository.

In **Settings > Actions > General**, retain read-only default workflow
permissions and allow Actions to create pull requests.  Protect `main` from
force pushes and require pull requests.  After the first package is published,
make `ghcr.io/mchehab/rasdaemon-ci` public.

## Synchronize rasdaemon

`make check-rasdaemon-sync` reports whether `mchehab/rasdaemon:master` differs
from `rasdaemon.lock.json`.  `make sync-rasdaemon` validates fast-forward
history and updates the lock.  The scheduled workflow performs the same
operation on `automation/sync-rasdaemon`, validates the resolved commit, and
opens or refreshes a reviewable pull request.

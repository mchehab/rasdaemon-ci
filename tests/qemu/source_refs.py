#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

"""Resolve and pin the external sources used by the QEMU test lab."""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile


DEFAULT_KERNEL_REPOSITORY = "https://github.com/torvalds/linux.git"
DEFAULT_QEMU_REPOSITORY = "https://gitlab.com/mchehab_kernel/qemu.git"
FINAL_TAG = re.compile(r"^v(\d+)\.(\d+)$")
RC_TAG = re.compile(r"^v(\d+)\.(\d+)-rc(\d+)$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class SourceError(RuntimeError):
    """An external source could not be resolved safely."""


def run(command, cwd=None):
    completed = subprocess.run(
        command, cwd=cwd, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if completed.returncode:
        raise SourceError("command failed: %s\n%s" %
                          (" ".join(command), completed.stdout.strip()))
    return completed.stdout


def ls_remote(repository, *patterns, include_peeled=False):
    command = ["git", "ls-remote"]
    if not include_peeled:
        command.append("--refs")
    output = run(command + [repository, *patterns])
    refs = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2 or not FULL_SHA.fullmatch(fields[0]):
            continue
        refs[fields[1]] = fields[0]
    return refs


def select_kernel_tag(refs, channel):
    pattern = FINAL_TAG if channel == "release" else RC_TAG
    candidates = []
    for ref in refs:
        name = ref.removeprefix("refs/tags/")
        match = pattern.fullmatch(name)
        if match:
            candidates.append((tuple(int(value) for value in match.groups()),
                               name))
    if not candidates:
        raise SourceError("no %s kernel tags found" % channel)
    return max(candidates)[1]


def resolve_ref(repository, ref):
    candidates = [ref]
    if not ref.startswith("refs/"):
        candidates.extend(("refs/heads/" + ref, "refs/tags/" + ref))
    refs = ls_remote(repository, *candidates,
                     include_peeled=any(value.startswith("refs/tags/")
                                        for value in candidates))
    for candidate in candidates:
        if candidate.startswith("refs/tags/") and candidate + "^{}" in refs:
            return refs[candidate + "^{}"], candidate
        if candidate in refs:
            return refs[candidate], candidate
    if FULL_SHA.fullmatch(ref):
        return ref, ref
    raise SourceError("%s does not advertise ref %s" % (repository, ref))


def resolve_kernel(repository, channel, ref=None):
    if channel == "custom":
        if not ref:
            raise SourceError("custom kernel selection requires --ref")
        commit, resolved_ref = resolve_ref(repository, ref)
        return {"repository": repository, "channel": channel,
                "ref": resolved_ref, "commit": commit}
    refs = ls_remote(repository, "refs/tags/v*")
    tag = select_kernel_tag(refs, channel)
    commit, resolved_ref = resolve_ref(repository, "refs/tags/" + tag)
    return {"repository": repository, "channel": channel,
            "ref": resolved_ref, "tag": tag, "commit": commit}


def load_lock(path):
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    required = {"repository", "ref", "commit"}
    if not required.issubset(value) or not FULL_SHA.fullmatch(value["commit"]):
        raise SourceError("invalid rasdaemon lock file")
    return value


def is_ancestor(repository, old, new):
    if old == new:
        return True
    with tempfile.TemporaryDirectory(prefix="rasdaemon-sync-") as directory:
        run(["git", "init", "-q", directory])
        run(["git", "remote", "add", "origin", repository], cwd=directory)
        run(["git", "fetch", "-q", "--no-tags", "--depth=1", "origin", old],
            cwd=directory)
        run(["git", "fetch", "-q", "--no-tags", "origin", new],
            cwd=directory)
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", old, new], cwd=directory,
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0


def sync_lock(path, update=False, verify_ancestry=True):
    lock = load_lock(path)
    new_commit, resolved_ref = resolve_ref(lock["repository"], lock["ref"])
    result = {"changed": new_commit != lock["commit"],
              "old_commit": lock["commit"], "new_commit": new_commit,
              "repository": lock["repository"], "ref": resolved_ref}
    if result["changed"] and verify_ancestry and not is_ancestor(
            lock["repository"], lock["commit"], new_commit):
        raise SourceError("upstream ref is not a fast-forward of the lock")
    if result["changed"] and update:
        lock["commit"] = new_commit
        pathlib.Path(path).write_text(
            json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def create_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    git_ref = commands.add_parser("resolve-git")
    git_ref.add_argument("--repository", required=True)
    git_ref.add_argument("--ref", required=True)
    kernel = commands.add_parser("resolve-kernel")
    kernel.add_argument("--repository", default=DEFAULT_KERNEL_REPOSITORY)
    kernel.add_argument("--channel", choices=("release", "rc", "custom"),
                        default="release")
    kernel.add_argument("--ref")
    sync = commands.add_parser("sync-rasdaemon")
    sync.add_argument("--lock", default="rasdaemon.lock.json")
    sync.add_argument("--update", action="store_true")
    sync.add_argument("--no-ancestry-check", action="store_true")
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    try:
        if args.command == "resolve-git":
            commit, ref = resolve_ref(args.repository, args.ref)
            result = {"repository": args.repository, "ref": ref,
                      "commit": commit}
        elif args.command == "resolve-kernel":
            result = resolve_kernel(args.repository, args.channel, args.ref)
        else:
            result = sync_lock(args.lock, args.update,
                               not args.no_ancestry_check)
        json.dump(result, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OSError, json.JSONDecodeError, SourceError) as error:
        print("source-refs: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

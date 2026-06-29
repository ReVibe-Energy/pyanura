#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "packaging>=26.0",
# ]
# ///
"""
reltool.py — A release management tool for Python projects.

Subcommands:
  update-version       Read VERSION, apply a bump/type, write VERSION
  validate-version     Verify VERSION against git tag state (GitHub Actions compatible)
  create-support-branch <tag>    Create a support branch from a stable release tag
  create-release-branch [<ref>]  Create a release branch from a dev commit
  tag-release                    Create a release commit + tag on the current branch

Must be run from the repository root.
"""

import argparse
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import packaging.version
from packaging.version import Version

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORT_PREFIX = "support/"  # support branch prefix  e.g. support/1.2
RELEASE_PREFIX = "release/"  # release branch prefix  e.g. release/1.3

_CYAN = "\033[0;36m"
_RESET = "\033[0m"


# ===========================================================================
# Git helpers
# ===========================================================================


def _git(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a git command.  Output is captured unless capture=False."""
    return subprocess.run(
        ["git", *args],
        capture_output=capture,
        text=True,
        check=check,
    )


def git_output(args: list[str]) -> str:
    """Run a git command and return its stripped stdout."""
    return _git(args).stdout.strip()


def git_do(args: list[str], dry_run: bool, description: str = "") -> None:
    """
    Run a mutating git command, letting its output flow to the terminal.
    In dry-run mode the command is only printed, never executed.
    Exits with a non-zero status on failure.
    """
    cmd_str = shlex.join(["git", *args])
    prefix = "[dry-run] " if dry_run else ""
    print(f"  {prefix}$ {cmd_str}")
    if not dry_run:
        result = subprocess.run(["git", *args], text=True)
        if result.returncode != 0:
            label = description or f"git {args[0]}"
            print(f"ERROR: {label} failed", file=sys.stderr)
            sys.exit(1)


def is_working_tree_clean() -> bool:
    """
    Return True when there are no staged, unstaged, or untracked changes in the
    working tree.
    """
    result = _git(["status", "--porcelain"], check=False)
    return result.returncode == 0 and not result.stdout.strip()


def resolve_ref(ref: str) -> str | None:
    """
    Return the full commit hash that *ref* points to, or None if it cannot
    be resolved.
    """
    result = _git(["rev-parse", "--verify", ref], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def current_branch() -> str:
    """Return the name of the currently checked-out branch."""
    return git_output(["rev-parse", "--abbrev-ref", "HEAD"])


def current_commit_hash(ref: str = "HEAD") -> str:
    """Return the abbreviated commit hash of *ref* (defaults to HEAD)."""
    return git_output(["rev-parse", "--short", ref])


def current_commit_tag(pattern: str = "v*") -> str | None:
    """Return the v* tag at the current commit, or None."""
    describe = _git(
        ["describe", "--tags", "--exact-match", "--match", pattern], check=False
    )
    if describe.returncode == 0:
        return describe.stdout.strip()
    else:
        return None


def branch_exists_local(name: str) -> bool:
    return bool(_git(["branch", "--list", name]).stdout.strip())


def branch_exists_remote(name: str) -> bool:
    """
    Return True when *name* exists on origin.
    Returns False (rather than raising) when origin is unavailable.
    """
    result = _git(["ls-remote", "--heads", "origin", name], check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def branch_exists(name: str) -> bool:
    """Return True when *name* exists locally or on origin."""
    return branch_exists_local(name) or branch_exists_remote(name)


def num_commits_since_last_tag(ref: str = "HEAD") -> int:
    """
    Return the number of commits since the last tag reachable from *ref*.
    Falls back to total commit count when no tags are found.
    """
    # --long:     forces the long output format: {TAG}-{COUNT}-g{HASH}
    # --tags:     considers lightweight tags in addition to annotated tags
    # --match v*: limits the search to tags prefixed with "v"
    describe = _git(["describe", "--long", "--tags", "--match", "v*", ref], check=False)
    if describe.returncode == 0:
        # The second-to-last field is the number of commits since the tag
        return int(describe.stdout.strip().split("-")[-2])
    else:
        return int(git_output(["rev-list", "--count", ref]))


# ===========================================================================
# Version state helpers
# ===========================================================================


def is_dev_version(ver: Version) -> bool:
    """True for versions like 1.3.dev0+local or 1.3rc1.dev0+local."""
    return ver.dev is not None and ver.local == "local"


def is_pre_release(ver: Version) -> bool:
    return ver.pre is not None


def is_final_release(ver: Version) -> bool:
    return ver.pre is None and ver.dev is None and ver.local is None


def bump_part(
    ver: Version, part: Literal["major", "minor", "patch", "rc", "b", "a"]
) -> Version:
    if part == "major":
        ver = ver.__replace__(release=(ver.major + 1, 0), pre=None)
    elif part == "minor":
        ver = ver.__replace__(release=(ver.major, ver.minor + 1), pre=None)
    elif part == "patch":
        ver = ver.__replace__(release=(ver.major, ver.minor, ver.micro + 1), pre=None)
    elif part in ("rc", "b", "a"):
        if ver.pre is not None and ver.pre[0] == part:
            ver = ver.__replace__(pre=(part, ver.pre[1] + 1))
        else:
            ver = ver.__replace__(pre=(part, 1))
    else:
        raise AssertionError("unreachable")

    return make_dev_version(ver)


def bump_auto(ver: Version) -> Version:
    if ver.pre is not None:
        assert ver.pre[0] in ("rc", "b", "a")
        part = ver.pre[0]
    elif ver.micro > 0:
        part = "patch"
    else:
        part = "minor"
    return bump_part(ver, part)


def make_final_release(ver: Version) -> Version:
    """
    Strip pre-release, dev and local parts to make a final release version.

    Example: 1.0.1rc2.dev0 -> 1.0.1
    """
    return ver.__replace__(pre=None, dev=None, local=None)


def make_pre_release(ver: Version) -> Version:
    """
    Strip dev and local parts to make a release version.

    Example: 1.0rc1.dev0 -> 1.0rc1
    """
    return ver.__replace__(dev=None, local=None)


def make_dev_version(ver: Version) -> Version:
    """
    Add development version marker (.dev0+local).

    Example: 1.0.1rc1 -> 1.0.1rc1.dev0+local
    """
    return ver.__replace__(dev=0, local="local")


def make_snapshot_release(ver: Version, ref: str = "HEAD") -> Version:
    """
    Add development version part and local info describing the current
    development state and timestamp.

    *ref* controls which commit is used for the commit count and hash.
    Pass ``HEAD^2`` to root the snapshot on the PR branch tip rather than
    on a GitHub-style merge commit.

    Example:
        If HEAD is 44 commits after the last v-tag:
            1.0.dev0+local -> 1.0.dev44+20260327214348.gdfee10d
    """
    dev_n = num_commits_since_last_tag(ref)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    git_hash = current_commit_hash(ref)
    return ver.__replace__(dev=dev_n, local=f"{timestamp}.g{git_hash}")


# ===========================================================================
# Reading and writing the VERSION file
# ===========================================================================


def get_version_at_ref(ref: str) -> Version:
    """Read the VERSION file content at the given git ref and parse it."""
    raw = git_output(["show", f"{ref}:VERSION"])
    return packaging.version.parse(raw.strip())


def get_current_version() -> Version:
    """Read and parse the VERSION file in the working directory."""
    try:
        return packaging.version.parse(Path("VERSION").read_text().strip())
    except FileNotFoundError:
        print("ERROR: VERSION file not found", file=sys.stderr)
        sys.exit(1)
    except packaging.version.InvalidVersion as exc:
        print(f"ERROR: VERSION is invalid: {exc}", file=sys.stderr)
        sys.exit(1)


def update_version_file(new: Version, dry_run: bool) -> None:
    current = get_current_version()

    if new < current:
        print(
            f"ERROR: New version {new} would precede current version {current}",
            file=sys.stderr,
        )
        sys.exit(1)

    prefix = "[dry-run] " if dry_run else ""
    if current != new:
        print(
            f"  {prefix}VERSION {_CYAN}{current}{_RESET} => {_CYAN}{new}{_RESET}",
            file=sys.stderr,
        )
        if not dry_run:
            Path("VERSION").write_text(f"{new}\n")
    else:
        print(
            f"  {prefix}VERSION {_CYAN}{current}{_RESET} (unmodified)",
            file=sys.stderr,
        )


# ===========================================================================
# Subcommand: update-version
# ===========================================================================


def cmd_update_version(args: argparse.Namespace) -> None:
    """Set or bump VERSION, keeping it in a dev state."""
    modes = [args.version is not None, bool(args.bump), args.snapshot]
    if sum(modes) > 1:
        print(
            "ERROR: version, --bump, and --snapshot are mutually exclusive.",
            file=sys.stderr,
        )
        sys.exit(1)
    if sum(modes) == 0:
        print(
            "ERROR: specify a version, --bump, or --snapshot.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.version is not None:
        try:
            base = packaging.version.parse(args.version)
        except packaging.version.InvalidVersion as exc:
            print(f"ERROR: invalid version '{args.version}': {exc}", file=sys.stderr)
            sys.exit(1)
        new = make_dev_version(base)
        update_version_file(new, dry_run=args.dry_run)

    elif args.bump:
        assert args.bump in ("major", "minor", "patch")
        new = bump_part(get_current_version(), args.bump)
        update_version_file(new, dry_run=args.dry_run)

    else:  # --snapshot
        tag = current_commit_tag()
        if tag is not None:
            try:
                tag_ver = packaging.version.parse(tag[1:])  # drop "v" prefix
            except packaging.version.InvalidVersion:
                tag_ver = None
            if tag_ver is not None:
                current = get_current_version()
                if current == tag_ver:
                    prefix = "[dry-run] " if args.dry_run else ""
                    print(
                        f"  {prefix}VERSION {_CYAN}{current}{_RESET}"
                        f" (clean release tag — skipping snapshot)",
                        file=sys.stderr,
                    )
                    return

        new = make_snapshot_release(get_current_version(), ref=args.ref or "HEAD")
        update_version_file(new, dry_run=args.dry_run)


# ===========================================================================
# Subcommand: validate-version
# ===========================================================================


def cmd_validate_version(args: argparse.Namespace) -> None:
    """
    Validate that VERSION matches the current git tag state.
    Replaces scripts/validate_version.py.
    Outputs GitHub Actions annotation-style error messages on failure.
    """
    ref_type: str | None = args.ref_type
    ref_name: str | None = args.ref_name

    print(f"Ref Type: {ref_type}")
    print(f"Ref Name: {ref_name}")

    ver = get_current_version()
    print(f"Version: {ver}")

    exact_tag = current_commit_tag()
    if exact_tag:
        print(f"Commit is tagged as: {exact_tag}")

    if ver.epoch > 0 or ver.is_postrelease:
        print("::error::VERSION contains post-release or epoch parts")
        sys.exit(1)

    # A build is considered a "Release Build" if:
    # 1. We are explicitly building a tag reference, or
    # 2. We are on a branch/PR but the current commit has a matching tag
    tag_name = ref_name if ref_type == "tag" else exact_tag

    if tag_name and tag_name.startswith("v"):
        # TAGGED / RELEASE BUILD RULES
        try:
            tag_ver = packaging.version.parse(tag_name[1:])  # strip leading 'v'
        except packaging.version.InvalidVersion as exc:
            print(f"::error::Parsing tag failed: {exc}")
            sys.exit(1)

        if ver.is_devrelease or ver.local:
            print(
                "::error::Release tag on a commit containing an unclean VERSION file:"
            )
            sys.exit(1)

        if ver != tag_ver:
            print(
                f"::error::Release tag '{tag_name}' does not semantically match "
                f"VERSION file content '{ver}'"
            )
            sys.exit(1)

        print(f"Success: Tag {tag_name} matches clean VERSION {ver}")
    else:
        # UNTAGGED / DEVELOPMENT BUILD RULES
        if ver.dev != 0:
            print(
                "::error::Development builds (untagged) must have 'dev0' in "
                "VERSION file"
            )
            sys.exit(1)

        print(f"Success: Development build with VERSION {ver}")


# ===========================================================================
# Subcommand: create-support-branch
# ===========================================================================


def cmd_create_support_branch(args: argparse.Namespace) -> None:
    """
    Create a support branch from a stable release tag and bump VERSION to
    the next patch dev version.

    Example: tag v1.2  →  branch support/1.2, VERSION 1.2.1.dev0+local
    """
    dry_run: bool = args.dry_run
    tag: str = args.tag

    print(f"Preparing support branch for tag '{tag}'")
    print()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    print("Validating...")

    if not is_working_tree_clean():
        print(
            "ERROR: Working tree is not clean. Commit or stash your changes first.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  ✓ Working tree is clean")

    commit = resolve_ref(tag)
    if commit is None:
        print(f"ERROR: Tag '{tag}' does not resolve to a commit.", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ Tag '{tag}' resolves to {commit[:8]}")

    try:
        ver = get_version_at_ref(tag)
    except Exception as exc:
        print(f"ERROR: Could not read VERSION at '{tag}': {exc}", file=sys.stderr)
        sys.exit(1)

    if not is_final_release(ver):
        print(
            f"ERROR: VERSION at '{tag}' is '{ver}', which is not a final release "
            "(must have no pre, dev, or local markers).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  ✓ VERSION at '{tag}' is {ver} (final release)")

    branch = f"{SUPPORT_PREFIX}{ver}"
    if branch_exists(branch):
        print(
            f"ERROR: Branch '{branch}' already exists locally or on origin.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  ✓ Branch '{branch}' does not exist")

    print()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    print("Executing...")

    # 1. Create and check out the support branch
    git_do(["branch", branch, tag], dry_run, f"create branch '{branch}'")
    git_do(["checkout", branch], dry_run, f"checkout '{branch}'")

    # 2. Bump version to next patch dev
    new_ver = bump_part(get_current_version(), "patch")
    update_version_file(new_ver, dry_run=dry_run)

    # 3. Commit
    commit_msg = f"Begin support branch for {tag}"
    git_do(["add", "VERSION"], dry_run, "stage VERSION")
    git_do(["commit", "-m", commit_msg], dry_run, "commit")

    # 4. Optionally push
    if args.push:
        git_do(["push", "origin", branch], dry_run, f"push '{branch}'")

    print()
    print(f"Done. Branch '{branch}' created at {new_ver}.")


# ===========================================================================
# Subcommand: create-release-branch
# ===========================================================================


def cmd_create_release_branch(args: argparse.Namespace) -> None:
    """
    Create a release branch from any dev commit on main or a support branch.

    Example: HEAD at 1.3.dev0+local  →  branch release/1.3, VERSION 1.3rc1.dev0+local
    """
    dry_run: bool = args.dry_run
    ref: str = args.ref or "HEAD"

    print(f"Creating release branch from '{ref}'")
    print()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    print("Validating...")

    commit = resolve_ref(ref)
    if commit is None:
        print(f"ERROR: Ref '{ref}' does not resolve to a commit.", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ Ref '{ref}' resolves to {commit[:8]}")

    try:
        ver = get_version_at_ref(ref)
    except Exception as exc:
        print(f"ERROR: Could not read VERSION at '{ref}': {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ VERSION at '{ref}' is {ver}")

    if not is_dev_version(ver):
        print(
            f"ERROR: VERSION at '{ref}' ({ver}) is not a dev version. "
            "Expected a version of the form X.Y.devN+local (e.g. 1.3.dev0+local).",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  ✓ Version is a dev version")

    # A release branch may be created from a plain dev version or from an
    # alpha/beta dev version (e.g. 1.0a5.dev0+local on main). An rc is rejected,
    # since an rc already lives on a release branch.
    if ver.pre is not None and ver.pre[0] == "rc":
        print(
            f"ERROR: VERSION at '{ref}' ({ver}) is a release candidate. "
            "Cannot create a new release branch from an rc state "
            "(it is already part of a release cycle).",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  ✓ Version is not a release candidate")

    # Derive the branch name by stripping pre-release, dev and local markers
    clean_ver = ver.__replace__(pre=None, dev=None, local=None)
    branch = f"{RELEASE_PREFIX}{clean_ver}"
    print(f"  ✓ Derived branch name: '{branch}'")

    if branch_exists(branch):
        print(
            f"ERROR: Branch '{branch}' already exists locally or on origin.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  ✓ Branch '{branch}' does not exist")

    print()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    print("Executing...")

    # 1. Create and check out the release branch
    git_do(["branch", branch, commit], dry_run, f"create branch '{branch}'")
    git_do(["checkout", branch], dry_run, f"checkout '{branch}'")

    # 2. Bump to first RC dev version
    new_ver = bump_part(get_current_version(), "rc")
    update_version_file(new_ver, dry_run=dry_run)

    # 3. Commit
    commit_msg = f"Create release branch, bump to {new_ver}"
    git_do(["add", "VERSION"], dry_run, "stage VERSION")
    git_do(["commit", "-m", commit_msg], dry_run, "commit")

    # 4. Optionally push
    if args.push:
        git_do(["push", "origin", branch], dry_run, f"push '{branch}'")

    print()
    print(f"Done. Branch '{branch}' created at {new_ver}.")


# ===========================================================================
# Subcommand: tag-release
# ===========================================================================


def cmd_tag_release(args: argparse.Namespace) -> None:
    """
    On the current branch, stamp a release commit + annotated tag, then
    immediately bump back to a dev version.

    --alpha / --beta (not on release/ branch):
      1.3.dev0+local → commit "Release 1.3a1", tag v1.3a1,
                        commit "Resume development at 1.3a2.dev0+local"

    --rc (on release/ branch):
      1.3rc1.dev0+local → commit "Release 1.3rc1", tag v1.3rc1,
                           commit "Resume development at 1.3rc2.dev0+local"

    (final, on release/ branch):
      1.3rc2.dev0+local → commit "Release 1.3", tag v1.3,
                           commit "Resume development at 1.4.dev0+local"
    """
    dry_run: bool = args.dry_run
    alpha: bool = args.alpha
    beta: bool = args.beta
    rc: bool = args.rc

    if alpha:
        mode = "alpha release"
    elif beta:
        mode = "beta release"
    elif rc:
        mode = "RC release"
    else:
        mode = "final release"

    print(f"Creating {mode} on current branch")
    print()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    print("Validating...")

    branch = current_branch()
    if alpha or beta:
        if branch.startswith(RELEASE_PREFIX):
            print(
                f"ERROR: Current branch '{branch}' starts with '{RELEASE_PREFIX}'. "
                "--alpha and --beta must not be run on a release branch.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  ✓ Not on a release branch ('{branch}')")
    else:  # --rc or final
        if not branch.startswith(RELEASE_PREFIX):
            print(
                f"ERROR: Current branch '{branch}' does not start with "
                f"'{RELEASE_PREFIX}'. Switch to a release/... branch first.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  ✓ On release branch '{branch}'")

    if not is_working_tree_clean():
        print(
            "ERROR: Working tree is not clean. Commit or stash your changes first.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  ✓ Working tree is clean")

    ver = get_current_version()
    print(f"  ✓ Current VERSION: {ver}")

    if not is_dev_version(ver):
        print(
            f"ERROR: VERSION ({ver}) is not a dev version. "
            "Expected a version of the form X.Y[rcN].dev0+local.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  ✓ VERSION is a dev version")

    print()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    print("Executing...")

    # Step 1 — Update VERSION to the release/pre version
    if alpha or beta or rc:
        pre_tag = "a" if alpha else "b" if beta else "rc"

        # If already at xxNdev, just strip dev; otherwise transition to xx1.
        if ver.pre is not None and ver.pre[0] == pre_tag:
            new_ver = make_pre_release(ver)
        else:
            new_ver = make_pre_release(bump_part(ver, pre_tag))
    else:
        new_ver = make_final_release(ver)
    update_version_file(new_ver, dry_run=dry_run)

    # Step 2 — Commit the release
    release_msg = f"Release {new_ver}"
    git_do(["add", "VERSION"], dry_run, "stage VERSION")
    git_do(["commit", "-m", release_msg], dry_run, "commit release")

    # Step 3 — Annotated tag
    tag = f"v{new_ver}"
    tag_msg = f"Release {tag}"
    git_do(["tag", "-a", tag, "-m", tag_msg], dry_run, f"create tag '{tag}'")
    print(f"  {'[dry-run] ' if dry_run else ''}Tagged {tag}")

    # Step 4 — Bump back to the next dev version
    dev_ver = bump_auto(new_ver)
    update_version_file(dev_ver, dry_run=dry_run)

    # Step 5 — Commit the dev bump
    resume_msg = f"Resume development at {dev_ver}"
    git_do(["add", "VERSION"], dry_run, "stage VERSION")
    git_do(["commit", "-m", resume_msg], dry_run, "commit resume")

    # Step 6 — Optionally push branch + tag
    if args.push:
        print()
        git_do(["push", "origin", branch], dry_run, f"push '{branch}'")
        git_do(["push", "origin", tag], dry_run, f"push tag '{tag}'")

    print()
    print(f"Done. Released {new_ver} ({tag}). Development continues at {dev_ver}.")


# ===========================================================================
# Argument parsing
# ===========================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reltool.py",
        description="A release management tool for Python projects.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every action without executing Git operations or writing files.",
    )

    sub = parser.add_subparsers(
        dest="subcommand", required=True, metavar="<subcommand>"
    )

    # ------------------------------------------------------------------
    # update-version
    # ------------------------------------------------------------------
    p_uv = sub.add_parser(
        "update-version",
        help="Set or bump VERSION, keeping it in a dev state.",
        description=(
            "Three modes (mutually exclusive):\n"
            "  update-version 2.2          set base version  → 2.2.dev0+local\n"
            "  update-version --bump PART  bump a component  → e.g. 2.0.dev0+local\n"
            "  update-version --snapshot   stamp a snapshot, or no-op on a clean release tag"  # noqa: E501
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_uv.add_argument(
        "version",
        nargs="?",
        default=None,
        metavar="VERSION",
        help="Explicit base version to set (e.g. 2.2 → 2.2.dev0+local).",
    )
    p_uv.add_argument(
        "--bump",
        choices=["major", "minor", "patch"],
        help="Bump a version component, keeping the dev suffix.",
    )
    p_uv.add_argument(
        "--snapshot",
        action="store_true",
        help=(
            "Stamp a snapshot version (devN+timestamp.gHASH). "
            "No-op if VERSION is already a clean release matching the current tag."
        ),
    )
    p_uv.add_argument(
        "--ref",
        default=None,
        metavar="REF",
        help=(
            "Git ref to root the snapshot count and hash on (default: HEAD). "
            "Pass the PR branch tip SHA to avoid counting commits from a "
            "synthetic merge commit."
        ),
    )
    p_uv.set_defaults(func=cmd_update_version)

    # ------------------------------------------------------------------
    # validate-version
    # ------------------------------------------------------------------
    p_vv = sub.add_parser(
        "validate-version",
        help="Verify VERSION against git tag state (GitHub Actions compatible).",
        description=(
            "Checks that VERSION matches the git tag when building from a tag,\n"
            "or that it is a dev version on untagged builds."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_vv.add_argument(
        "ref_type",
        nargs="?",
        default=None,
        help='GitHub ref type, e.g. "tag" or "branch" (${{ github.ref_type }}).',
    )
    p_vv.add_argument(
        "ref_name",
        nargs="?",
        default=None,
        help='GitHub ref name, e.g. "v1.3" (${{ github.ref_name }}).',
    )
    p_vv.set_defaults(func=cmd_validate_version)

    # ------------------------------------------------------------------
    # create-support-branch
    # ------------------------------------------------------------------
    p_maint = sub.add_parser(
        "create-support-branch",
        help="Create a support branch from a stable release tag.",
        description=(
            "Creates support/{version} at the given tag and bumps VERSION\n"
            "to the next patch dev version (e.g. v1.2 → 1.2.1.dev0+local)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_maint.add_argument(
        "tag",
        help="Git tag to branch from, e.g. v1.2 (must be a final release).",
    )
    p_maint.add_argument(
        "--push",
        action="store_true",
        help="Push the new branch to origin after creation.",
    )
    p_maint.set_defaults(func=cmd_create_support_branch)

    # ------------------------------------------------------------------
    # create-release-branch
    # ------------------------------------------------------------------
    p_branch = sub.add_parser(
        "create-release-branch",
        help="Create a release branch from a dev commit.",
        description=(
            "Creates release/{version} at <REF> and bumps VERSION to the first\n"
            "RC dev version (e.g. 1.3.dev0+local → 1.3rc1.dev0+local)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_branch.add_argument(
        "ref",
        nargs="?",
        default=None,
        help=(
            "Git ref to branch from (commit hash, branch name, tag). "
            "Defaults to HEAD."
        ),
    )
    p_branch.add_argument(
        "--push",
        action="store_true",
        help="Push the new branch to origin after creation.",
    )
    p_branch.set_defaults(func=cmd_create_release_branch)

    # ------------------------------------------------------------------
    # tag-release
    # ------------------------------------------------------------------
    p_release = sub.add_parser(
        "tag-release",
        help="Create a release commit and tag on the current branch.",
        description=(
            "Stamps a release commit + annotated tag, then bumps back to dev.\n"
            "Omit all flags for a final release (must be on a release/... branch).\n"
            "--alpha and --beta may be run on any non-release branch.\n"
            "--rc must be run on a release/... branch."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _pre_group = p_release.add_mutually_exclusive_group()
    _pre_group.add_argument(
        "--alpha",
        action="store_true",
        help=(
            "Create an alpha pre-release (bumps alpha counter, not on "
            "release/ branch)."
        ),
    )
    _pre_group.add_argument(
        "--beta",
        action="store_true",
        help="Create a beta pre-release (bumps beta counter, not on release/ branch).",
    )
    _pre_group.add_argument(
        "--rc",
        action="store_true",
        help="Create a release candidate (must be on a release/... branch).",
    )
    p_release.add_argument(
        "--push",
        action="store_true",
        help="Push the branch and tag to origin after the release.",
    )
    p_release.set_defaults(func=cmd_tag_release)

    return parser


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

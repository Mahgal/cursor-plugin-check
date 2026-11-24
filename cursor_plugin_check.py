#!/usr/bin/env python3
"""cursor-plugin-check: validate a Cursor plugin against the official spec.

A zero-dependency, offline `cargo check`-style entry point for
`.cursor-plugin/plugin.json` + directory structure. It enforces the rules
in cursor/plugins `schemas/plugin.schema.json` (fetched live 2026-11-24)
and the conventions every official plugin in that repo follows
(16/16 sampled 2026-11-24: description, version, displayName present;
name == directory name; categories observed: developer-tools, utilities).

Usage:
    python3 cursor_plugin_check.py <plugin-dir>   # exit 0 = all pass, 1 = any FAIL
    python3 cursor_plugin_check.py --demo         # bundled fixtures, exit 0
    python3 cursor_plugin_check.py --allow-category my-category <plugin-dir>

Checks are split into two groups, shown in the table:
  schema  - enforced by schemas/plugin.schema.json (required: name only,
            additionalProperties: false, name pattern, strict semver for
            minClientVersions values)
  policy  - enforced by this tool because all 16 official plugins ship
            them (description, displayName, version) or because a broken
            reference can only fail at install time (dangling skills /
            commands / agents / rules / hooks / logo paths, duplicate
            command names, name != directory name).

Exit codes: 0 = all checks pass; 1 = at least one FAIL (by design);
2 = usage error (missing directory, unreadable path).
"""
import glob
import json
import os
import re
import sys

VERSION = "1.0.0"

# --- Ground truth: cursor/plugins schemas/plugin.schema.json (live, 2026-11-24)
# $id: https://cursor.com/schemas/cursor-plugin/plugin.json
SCHEMA_PROPERTIES = {
    "name", "displayName", "description", "version", "minClientVersions",
    "author", "publisher", "homepage", "repository", "license", "logo",
    "keywords", "category", "tags", "commands", "agents", "skills",
    "rules", "hooks", "variables", "mcpServers",
}
NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
# Strict semver from the schema's $defs/semver (X.Y.Z + optional prerelease).
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?$"
)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Categories actually used by the 16 official plugins sampled 2026-11-24.
# The schema itself has no enum for category, so anything outside this
# observed set FAILs with a pointer to --allow-category.
KNOWN_CATEGORIES = {"developer-tools", "utilities"}
REF_FIELDS = ("commands", "agents", "skills", "rules")


class Check:
    """One row of the report: code, group, status, detail."""

    def __init__(self, code, group, status, detail):
        self.code = code
        self.group = group
        self.status = status  # PASS | FAIL | SKIP
        self.detail = detail


def _is_rel_path(value):
    return isinstance(value, str) and value and not os.path.isabs(value)


def _resolve_refs(root, entry):
    """Resolve one commands/agents/skills/rules entry to existing files.

    Entries in official plugins are directory paths ("./skills/"), file
    paths, or globs ("skills/*.md"). Returns (files, error-or-None).
    """
    if not _is_rel_path(entry):
        return [], "entry must be a relative path, got %r" % (entry,)
    clean = entry[2:] if entry.startswith("./") else entry
    target = os.path.join(root, clean)
    if os.path.isdir(target):
        found = []
        for dirpath, _dirs, filenames in os.walk(target):
            for name in sorted(filenames):
                found.append(os.path.join(dirpath, name))
        if not found:
            return [], "directory %s is empty" % (entry,)
        return sorted(found), None
    matches = sorted(glob.glob(target))
    if matches:
        files = [m for m in matches if os.path.isfile(m)]
        if files:
            return files, None
        return [], "matches no file: %s" % (entry,)
    if os.path.isfile(target):
        return [target], None
    return [], "no such file or directory: %s" % (entry,)


def check_plugin(root, allow_categories=frozenset()):
    """Run every check against the plugin directory at `root`.

    Returns a list of Check objects in fixed order (deterministic output).
    """
    checks = []
    manifest_path = os.path.join(root, ".cursor-plugin", "plugin.json")
    if not os.path.isfile(manifest_path):
        checks.append(Check("manifest", "schema", "FAIL",
                             ".cursor-plugin/plugin.json not found"))
        checks.append(Check("manifest-json", "schema", "SKIP",
                             "cannot check: manifest unreadable"))
        for code, group in _later_checks():
            checks.append(Check(code, group, "SKIP",
                                "cannot check: manifest unreadable"))
        return checks
    checks.append(Check("manifest", "schema", "PASS",
                         ".cursor-plugin/plugin.json found"))
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        checks.append(Check("manifest-json", "schema", "FAIL",
                             "invalid JSON: %s" % (exc.msg if hasattr(exc, "msg") else exc,)))
        for code, group in _later_checks():
            checks.append(Check(code, group, "SKIP",
                                "cannot check: manifest unreadable"))
        return checks
    if not isinstance(manifest, dict):
        checks.append(Check("manifest-json", "schema", "FAIL",
                             "plugin.json must be a JSON object"))
        for code, group in _later_checks():
            checks.append(Check(code, group, "SKIP",
                                "cannot check: manifest unreadable"))
        return checks
    checks.append(Check("manifest-json", "schema", "PASS",
                         "valid JSON object, %d top-level keys" % len(manifest)))

    # 3. name required (the schema's only required field).
    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        checks.append(Check("name-required", "schema", "FAIL",
                             "'name' is required by the schema"))
    else:
        checks.append(Check("name-required", "schema", "PASS",
                             "name=%s" % name))

    # 4. name format: ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ (schema pattern).
    if isinstance(name, str) and name:
        if NAME_PATTERN.match(name):
            checks.append(Check("name-format", "schema", "PASS",
                                 "kebab-case, matches schema pattern"))
        else:
            checks.append(Check("name-format", "schema", "FAIL",
                                 "name %r must be kebab-case "
                                 "(lowercase letters, digits, -, .)" % name))

    # 5. name == directory name (16/16 official plugins).
    if isinstance(name, str) and name:
        dirname = os.path.basename(os.path.abspath(root))
        if name == dirname:
            checks.append(Check("name-matches-dir", "policy", "PASS",
                                 "name matches directory '%s'" % dirname))
        else:
            checks.append(Check("name-matches-dir", "policy", "FAIL",
                                 "name %r != directory %r" % (name, dirname)))

    # 6. additionalProperties: false — no unknown top-level keys.
    unknown = sorted(set(manifest) - SCHEMA_PROPERTIES)
    if unknown:
        checks.append(Check("no-unknown-fields", "schema", "FAIL",
                             "unknown field(s): %s" % ", ".join(unknown)))
    else:
        checks.append(Check("no-unknown-fields", "schema", "PASS",
                             "no fields outside the schema"))

    # 7-9. Fields every official plugin ships (16/16 sampled 2026-11-24).
    for field, code in (("description", "description-present"),
                        ("displayName", "display-name-present"),
                        ("version", "version-present")):
        value = manifest.get(field)
        if isinstance(value, str) and value.strip():
            checks.append(Check(code, "policy", "PASS",
                                 "%s present" % field))
        else:
            checks.append(Check(code, "policy", "FAIL",
                                 "%s missing (present in 16/16 official "
                                 "plugins)" % field))

    # 10. version must be strict semver (schema describes it as such).
    version = manifest.get("version")
    if isinstance(version, str) and version.strip():
        if SEMVER_PATTERN.match(version):
            checks.append(Check("version-semver", "schema", "PASS",
                                 "version %s is valid semver" % version))
        else:
            checks.append(Check("version-semver", "schema", "FAIL",
                                 "version %r is not semver X.Y.Z" % version))

    # 11. category: schema has no enum; FAIL only outside the observed set.
    category = manifest.get("category")
    if category is None:
        checks.append(Check("category-known", "policy", "PASS",
                             "category not set (optional)"))
    elif not isinstance(category, str) or not category.strip():
        checks.append(Check("category-known", "policy", "FAIL",
                             "category must be a non-empty string"))
    elif category in KNOWN_CATEGORIES or category in allow_categories:
        checks.append(Check("category-known", "policy", "PASS",
                             "category '%s' used by official plugins"
                             % category))
    else:
        checks.append(Check("category-known", "policy", "FAIL",
                             "category %r not used by any official plugin "
                             "(observed: %s); use --allow-category to accept "
                             "it" % (category,
                                     ", ".join(sorted(KNOWN_CATEGORIES)))))

    # 12. author: object, name required, email must look like an email.
    author = manifest.get("author")
    if author is None:
        checks.append(Check("author-valid", "policy", "PASS",
                             "author not set (optional)"))
    elif not isinstance(author, dict):
        checks.append(Check("author-valid", "schema", "FAIL",
                             "author must be an object"))
    elif not isinstance(author.get("name"), str) or not author["name"].strip():
        checks.append(Check("author-valid", "schema", "FAIL",
                             "author.name is required"))
    elif set(author) - {"name", "email"}:
        checks.append(Check("author-valid", "schema", "FAIL",
                             "author has unknown field(s): %s"
                             % ", ".join(sorted(set(author) - {"name", "email"}))))
    elif "email" in author and not EMAIL_PATTERN.match(author["email"]):
        checks.append(Check("author-valid", "schema", "FAIL",
                             "author.email %r is not an email address"
                             % author["email"]))
    else:
        checks.append(Check("author-valid", "schema", "PASS",
                             "author %s" % author["name"]))

    # 13. homepage / repository must be http(s) URLs (schema format: uri).
    bad_urls = []
    for field in ("homepage", "repository"):
        value = manifest.get(field)
        if value is not None and (
                not isinstance(value, str)
                or not re.match(r"^https?://[^/\s]+\.[^/\s]+", value)):
            bad_urls.append("%s=%r" % (field, value))
    if bad_urls:
        checks.append(Check("urls-valid", "schema", "FAIL",
                             "not http(s) URLs: %s" % "; ".join(bad_urls)))
    else:
        checks.append(Check("urls-valid", "schema", "PASS",
                             "homepage/repository URLs look valid"))

    # 14. keywords / tags must be arrays of non-empty strings.
    bad_lists = []
    for field in ("keywords", "tags"):
        value = manifest.get(field)
        if value is not None and (
                not isinstance(value, list)
                or not all(isinstance(v, str) and v.strip() for v in value)):
            bad_lists.append(field)
    if bad_lists:
        checks.append(Check("keywords-tags-valid", "schema", "FAIL",
                             "%s must be an array of non-empty strings"
                             % ", ".join(bad_lists)))
    else:
        checks.append(Check("keywords-tags-valid", "schema", "PASS",
                             "keywords/tags are string arrays"))

    # 15. minClientVersions values: strict semver or the literal "never".
    mcv = manifest.get("minClientVersions")
    if mcv is None:
        checks.append(Check("min-client-versions", "schema", "PASS",
                             "minClientVersions not set (optional)"))
    elif not isinstance(mcv, dict) or not mcv:
        checks.append(Check("min-client-versions", "schema", "FAIL",
                             "minClientVersions must be a non-empty object"))
    else:
        bad = sorted(k for k, v in mcv.items()
                     if not (isinstance(v, str)
                             and (v == "never" or SEMVER_PATTERN.match(v))))
        if bad:
            checks.append(Check("min-client-versions", "schema", "FAIL",
                                 "bad version requirement(s): %s"
                                 % ", ".join(bad)))
        else:
            checks.append(Check("min-client-versions", "schema", "PASS",
                                 "%d client requirement(s) valid"
                                 % len(mcv)))

    # 16. variables: object whose "type" must be "object" (schema).
    variables = manifest.get("variables")
    if variables is None:
        checks.append(Check("variables-valid", "schema", "PASS",
                             "variables not set (optional)"))
    elif not isinstance(variables, dict):
        checks.append(Check("variables-valid", "schema", "FAIL",
                             "variables must be an object"))
    elif variables.get("type") != "object":
        checks.append(Check("variables-valid", "schema", "FAIL",
                             'variables.type must be "object"'))
    else:
        checks.append(Check("variables-valid", "schema", "PASS",
                             "variables schema looks valid"))

    # 17. Referenced skills / commands / agents / rules must exist.
    missing = []
    resolved = {}  # field -> {entry: [files]}
    for field in REF_FIELDS:
        value = manifest.get(field)
        if value is None:
            continue
        entries = value if isinstance(value, list) else [value]
        if not isinstance(value, (str, list)) or not all(
                isinstance(e, str) for e in entries):
            missing.append("%s: must be a string or array of strings" % field)
            continue
        resolved[field] = {}
        for entry in entries:
            files, error = _resolve_refs(root, entry)
            if error:
                missing.append("%s: %s" % (field, error))
            else:
                resolved[field][entry] = files
    if missing:
        checks.append(Check("refs-exist", "policy", "FAIL",
                             "; ".join(missing)))
    else:
        total = sum(len(fs) for by_field in resolved.values()
                    for fs in by_field.values())
        checks.append(Check("refs-exist", "policy", "PASS",
                             "%d referenced file(s) found"
                             % total if resolved else
                             "no skills/commands/agents/rules referenced"))

    # 18. hooks: a string path must point at an existing file.
    hooks = manifest.get("hooks")
    if hooks is None:
        checks.append(Check("hooks-valid", "policy", "PASS",
                             "hooks not set (optional)"))
    elif isinstance(hooks, dict):
        checks.append(Check("hooks-valid", "policy", "PASS",
                             "inline hooks object"))
    elif isinstance(hooks, str):
        clean = hooks[2:] if hooks.startswith("./") else hooks
        if os.path.isfile(os.path.join(root, clean)):
            checks.append(Check("hooks-valid", "policy", "PASS",
                                 "hooks file %s exists" % hooks))
        else:
            checks.append(Check("hooks-valid", "policy", "FAIL",
                                 "hooks file not found: %s" % hooks))
    else:
        checks.append(Check("hooks-valid", "schema", "FAIL",
                             "hooks must be a path string or an object"))

    # 19. mcpServers: a string path must point at an existing file.
    mcp = manifest.get("mcpServers")
    if mcp is None:
        checks.append(Check("mcp-servers-valid", "policy", "PASS",
                             "mcpServers not set (optional)"))
    elif isinstance(mcp, str):
        clean = mcp[2:] if mcp.startswith("./") else mcp
        if os.path.isfile(os.path.join(root, clean)):
            checks.append(Check("mcp-servers-valid", "policy", "PASS",
                                 "mcpServers file %s exists" % mcp))
        else:
            checks.append(Check("mcp-servers-valid", "policy", "FAIL",
                                 "mcpServers file not found: %s" % mcp))
    elif isinstance(mcp, (dict, list)):
        checks.append(Check("mcp-servers-valid", "policy", "PASS",
                             "inline mcpServers config"))
    else:
        checks.append(Check("mcp-servers-valid", "schema", "FAIL",
                             "mcpServers must be a path, object, or array"))

    # 20. logo: a relative path must point at an existing file.
    logo = manifest.get("logo")
    if logo is None:
        checks.append(Check("logo-exists", "policy", "PASS",
                             "logo not set (optional)"))
    elif not isinstance(logo, str) or not logo:
        checks.append(Check("logo-exists", "policy", "FAIL",
                             "logo must be a non-empty string"))
    elif logo.startswith(("http://", "https://")):
        checks.append(Check("logo-exists", "policy", "PASS",
                             "logo is a remote URL"))
    else:
        clean = logo[2:] if logo.startswith("./") else logo
        if os.path.isfile(os.path.join(root, clean)):
            checks.append(Check("logo-exists", "policy", "PASS",
                                 "logo file %s exists" % logo))
        else:
            checks.append(Check("logo-exists", "policy", "FAIL",
                                 "logo file not found: %s" % logo))

    # 21. Duplicate command names: duplicate entries, duplicate resolved
    # files, or two command files sharing a stem (the name Cursor shows).
    dupes = []
    cmds = manifest.get("commands")
    if isinstance(cmds, (str, list)):
        entries = cmds if isinstance(cmds, list) else [cmds]
        seen_entries = set()
        for entry in entries:
            if entry in seen_entries:
                dupes.append("duplicate commands entry: %s" % entry)
            seen_entries.add(entry)
        cmd_files = resolved.get("commands", {})
        all_files = [f for fs in cmd_files.values() for f in fs]
        if len(all_files) != len(set(all_files)):
            dup = sorted({f for f in all_files if all_files.count(f) > 1})
            dupes.append("same file referenced twice: %s"
                         % ", ".join(os.path.basename(f) for f in dup))
        stems = {}
        for f in all_files:
            stem = os.path.splitext(os.path.basename(f))[0]
            stems.setdefault(stem, []).append(f)
        for stem, files in sorted(stems.items()):
            if len(files) > 1:
                dupes.append("duplicate command name %r: %s"
                             % (stem, ", ".join(
                                 os.path.basename(f) for f in files)))
    if dupes:
        checks.append(Check("no-duplicate-commands", "policy", "FAIL",
                             "; ".join(dupes)))
    else:
        checks.append(Check("no-duplicate-commands", "policy", "PASS",
                             "no duplicate command names"))
    return checks


def _later_checks():
    # Checks after manifest-json (already emitted by the caller).
    return [
        ("name-required", "schema"),
        ("name-format", "schema"),
        ("name-matches-dir", "policy"),
        ("no-unknown-fields", "schema"),
        ("description-present", "policy"),
        ("display-name-present", "policy"),
        ("version-present", "policy"),
        ("version-semver", "schema"),
        ("category-known", "policy"),
        ("author-valid", "schema"),
        ("urls-valid", "schema"),
        ("keywords-tags-valid", "schema"),
        ("min-client-versions", "schema"),
        ("variables-valid", "schema"),
        ("refs-exist", "policy"),
        ("hooks-valid", "policy"),
        ("mcp-servers-valid", "policy"),
        ("logo-exists", "policy"),
        ("no-duplicate-commands", "policy"),
    ]


def render_table(path, checks):
    """Render one plugin's result table. Deterministic: no timestamps,
    no absolute paths — `path` is printed exactly as given."""
    lines = ["cursor-plugin-check  %s" % path,
             "-" * 72]
    for c in checks:
        lines.append("%-4s  %-22s %s" % (c.status, c.code, c.detail))
    passed = sum(1 for c in checks if c.status == "PASS")
    failed = sum(1 for c in checks if c.status == "FAIL")
    skipped = sum(1 for c in checks if c.status == "SKIP")
    verdict = "PASS" if failed == 0 else "FAIL"
    lines.append("-" * 72)
    lines.append("%d passed, %d failed, %d skipped -- %s"
                 % (passed, failed, skipped, verdict))
    return "\n".join(lines) + "\n"


def run_demo():
    """One-screen showcase (fits the 34-line hero render): the bad
    fixture's failing rows, then the good fixture's full PASS table.
    Full per-check tables are one command away:
    `cursor_plugin_check.py fixtures/bad-plugin`.
    Exit code is always 0 (the demo itself succeeds — the bad fixture
    failing is the point)."""
    base = os.path.dirname(os.path.abspath(__file__))
    lines = ["cursor-plugin-check --demo", ""]
    bad = check_plugin(os.path.join(base, "fixtures/bad-plugin"))
    failed = [c for c in bad if c.status == "FAIL"]
    lines.append("fixtures/bad-plugin -- %d check(s) failed:"
                 % len(failed))
    for c in failed:
        lines.append("%-4s  %-22s %s" % (c.status, c.code, c.detail))
    lines.append("%d passed, %d failed, 0 skipped -- FAIL"
                 % (sum(1 for c in bad if c.status == "PASS"), len(failed)))
    lines.append("")
    good = check_plugin(os.path.join(base, "fixtures/good-plugin"))
    lines.append("fixtures/good-plugin -- all %d checks passed:"
                 % len(good))
    for c in good:
        lines.append("%-4s  %-22s %s" % (c.status, c.code, c.detail))
    lines.append("%d passed, 0 failed, 0 skipped -- PASS" % len(good))
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def main(argv):
    allow = set()
    args = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--demo":
            return run_demo()
        if arg == "--allow-category":
            if i + 1 >= len(argv):
                print("error: --allow-category needs a value",
                      file=sys.stderr)
                return 2
            allow.add(argv[i + 1])
            i += 2
            continue
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        if arg == "--version":
            print("cursor-plugin-check %s" % VERSION)
            return 0
        if arg.startswith("-"):
            print("error: unknown option %s" % arg, file=sys.stderr)
            return 2
        args.append(arg)
        i += 1
    if len(args) != 1:
        print("usage: cursor_plugin_check.py [--allow-category CAT] "
              "<plugin-dir>", file=sys.stderr)
        print("       cursor_plugin_check.py --demo", file=sys.stderr)
        return 2
    root = args[0]
    if not os.path.isdir(root):
        print("error: not a directory: %s" % root, file=sys.stderr)
        return 2
    checks = check_plugin(root, frozenset(allow))
    sys.stdout.write(render_table(root, checks))
    return 0 if not any(c.status == "FAIL" for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

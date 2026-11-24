#!/usr/bin/env python3
"""Tests for cursor_plugin_check.py (stdlib unittest only).

Run:  python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "cursor_plugin_check.py")
FIXTURES = os.path.join(ROOT, "fixtures")

GOOD_MANIFEST = {
    "name": "good-plugin",
    "displayName": "Good Plugin",
    "description": "A good plugin.",
    "version": "1.0.0",
    "category": "developer-tools",
    "author": {"name": "Example Author"},
    "commands": "./commands/",
    "skills": "./skills/",
}


def run_cli(*args):
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True)


def write_plugin(base, manifest, files=None):
    """Create a plugin dir named after manifest['name'] under base."""
    plug = os.path.join(base, manifest["name"])
    os.makedirs(os.path.join(plug, ".cursor-plugin"), exist_ok=True)
    with open(os.path.join(plug, ".cursor-plugin", "plugin.json"),
              "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    for rel, content in (files or {}).items():
        path = os.path.join(plug, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return plug


class TestFixtures(unittest.TestCase):
    def test_good_fixture_passes_exit_0(self):
        r = run_cli(os.path.join(FIXTURES, "good-plugin"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("21 passed, 0 failed, 0 skipped -- PASS", r.stdout)

    def test_bad_fixture_fails_exit_1(self):
        r = run_cli(os.path.join(FIXTURES, "bad-plugin"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("17 passed, 4 failed, 0 skipped -- FAIL", r.stdout)
        for code in ("description-present", "category-known",
                     "refs-exist", "no-duplicate-commands"):
            self.assertIn("FAIL  %s" % code, r.stdout)

    def test_demo_exit_0_and_deterministic(self):
        outs = []
        for _ in range(2):
            r = run_cli("--demo")
            self.assertEqual(r.returncode, 0, r.stdout)
            outs.append(r.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(hashlib.sha256(outs[0].encode()).hexdigest(),
                         hashlib.sha256(outs[1].encode()).hexdigest())
        self.assertIn("fixtures/bad-plugin", outs[0])
        self.assertIn("fixtures/good-plugin", outs[0])
        # Demo output must not contain absolute paths or timestamps.
        self.assertNotIn(ROOT, outs[0])
        self.assertNotIn("/tmp", outs[0])


class TestManifestErrors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_empty_directory(self):
        empty = os.path.join(self.tmp, "empty-plugin")
        os.makedirs(empty)
        r = run_cli(empty)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  manifest", r.stdout)
        self.assertIn("SKIP", r.stdout)

    def test_missing_directory_is_usage_error(self):
        r = run_cli(os.path.join(self.tmp, "no-such-dir"))
        self.assertEqual(r.returncode, 2)

    def test_broken_json(self):
        plug = os.path.join(self.tmp, "broken-plugin")
        os.makedirs(os.path.join(plug, ".cursor-plugin"))
        with open(os.path.join(plug, ".cursor-plugin", "plugin.json"),
                  "w", encoding="utf-8") as fh:
            fh.write('{"name": "broken-plugin", ')
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  manifest-json", r.stdout)
        self.assertIn("invalid JSON", r.stdout)

    def test_non_object_json(self):
        plug = os.path.join(self.tmp, "list-plugin")
        os.makedirs(os.path.join(plug, ".cursor-plugin"))
        with open(os.path.join(plug, ".cursor-plugin", "plugin.json"),
                  "w", encoding="utf-8") as fh:
            fh.write('["not", "an", "object"]')
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be a JSON object", r.stdout)

    def test_missing_name(self):
        m = dict(GOOD_MANIFEST)
        del m["name"]
        plug = os.path.join(self.tmp, "noname-plugin")
        os.makedirs(os.path.join(plug, ".cursor-plugin"), exist_ok=True)
        with open(os.path.join(plug, ".cursor-plugin", "plugin.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  name-required", r.stdout)

    def test_missing_description(self):
        m = dict(GOOD_MANIFEST)
        del m["description"]
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  description-present", r.stdout)

    def test_unknown_field(self):
        m = dict(GOOD_MANIFEST)
        m["surpriseField"] = True
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  no-unknown-fields", r.stdout)
        self.assertIn("surpriseField", r.stdout)

    def test_invalid_name_pattern(self):
        m = dict(GOOD_MANIFEST)
        m["name"] = "Bad_Name!"
        plug = write_plugin(self.tmp, m)
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  name-format", r.stdout)

    def test_name_mismatch_with_directory(self):
        m = dict(GOOD_MANIFEST)
        m["name"] = "other-name"
        plug = write_plugin(self.tmp, m)
        # write_plugin created dir "other-name"; rename it to mismatch.
        renamed = os.path.join(self.tmp, "good-plugin")
        os.rename(plug, renamed)
        r = run_cli(renamed)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  name-matches-dir", r.stdout)

    def test_invalid_version_semver(self):
        m = dict(GOOD_MANIFEST)
        m["version"] = "1.0"
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  version-semver", r.stdout)

    def test_unknown_category_rejected_and_allowlisted(self):
        m = dict(GOOD_MANIFEST)
        m["category"] = "productivity"
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  category-known", r.stdout)
        r = run_cli("--allow-category", "productivity", plug)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("PASS  category-known", r.stdout)


class TestReferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_missing_referenced_skill_file(self):
        m = dict(GOOD_MANIFEST)
        m["skills"] = "./skills/missing.md"
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  refs-exist", r.stdout)
        self.assertIn("missing.md", r.stdout)

    def test_glob_reference_with_no_match(self):
        m = dict(GOOD_MANIFEST)
        m["commands"] = "commands/*.md"
        plug = write_plugin(self.tmp, m,
                            {"skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  refs-exist", r.stdout)

    def test_duplicate_command_names_same_stem(self):
        m = dict(GOOD_MANIFEST)
        m["commands"] = ["./commands/a/", "./commands/b/"]
        plug = write_plugin(self.tmp, m,
                            {"commands/a/greet.md": "# a\n",
                             "commands/b/greet.md": "# b\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  no-duplicate-commands", r.stdout)
        self.assertIn("greet", r.stdout)

    def test_duplicate_command_entries(self):
        m = dict(GOOD_MANIFEST)
        m["commands"] = ["./commands/", "./commands/"]
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("duplicate commands entry", r.stdout)

    def test_hooks_file_missing(self):
        m = dict(GOOD_MANIFEST)
        m["hooks"] = "./hooks/hooks.json"
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  hooks-valid", r.stdout)

    def test_min_client_versions_never_and_bad(self):
        m = dict(GOOD_MANIFEST)
        m["minClientVersions"] = {"cursor": "never", "grokbot": "0.49.0"}
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 0, r.stdout)
        m["minClientVersions"] = {"cursor": "soon"}
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  min-client-versions", r.stdout)

    def test_good_plugin_with_all_optional_fields(self):
        m = dict(GOOD_MANIFEST)
        m.update({
            "homepage": "https://example.com",
            "repository": "https://github.com/example-org/good-plugin",
            "license": "MIT",
            "publisher": "example-org",
            "keywords": ["greeting"],
            "tags": ["demo"],
            "minClientVersions": {"cursor": "3.0.0"},
            "variables": {"type": "object", "properties": {}},
            "hooks": "./hooks/hooks.json",
            "logo": "./logo.png",
        })
        plug = write_plugin(self.tmp, m,
                            {"commands/hello.md": "# hi\n",
                             "skills/greeter/SKILL.md": "# skill\n",
                             "hooks/hooks.json": "{}\n",
                             "logo.png": "not a real png, just a fixture\n"})
        r = run_cli(plug)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("21 passed, 0 failed, 0 skipped -- PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()

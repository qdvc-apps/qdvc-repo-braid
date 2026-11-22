"""Tests for qdvc-repo-braid. Run from the project root:

    python3 -m unittest discover -s tests -v

Each test builds small throwaway repositories with fixed dates, so results
are deterministic. Your own Git config is ignored (GIT_CONFIG_GLOBAL is
pointed at a temporary file) so things like commit signing don't interfere.
"""

import importlib.util
import importlib.machinery
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "qdvc-repo-braid")

# The script has no .py extension (so it can be symlinked as a Git
# subcommand), so load it explicitly for the unit tests.
_loader = importlib.machinery.SourceFileLoader("qdvc_repo_braid", SCRIPT)
_spec = importlib.util.spec_from_loader("qdvc_repo_braid", _loader)
gb = importlib.util.module_from_spec(_spec)
_loader.exec_module(gb)

_saved_env = {}


def setUpModule():
    global CONFIG_DIR
    CONFIG_DIR = tempfile.mkdtemp(prefix="braid-cfg-")
    cfg = os.path.join(CONFIG_DIR, "gitconfig")
    with open(cfg, "w") as fh:
        fh.write("[user]\n\tname = Test\n\temail = test@example.com\n"
                 "[init]\n\tdefaultBranch = main\n"
                 "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n")
    for key, value in (("GIT_CONFIG_GLOBAL", cfg),
                       ("GIT_CONFIG_NOSYSTEM", "1")):
        _saved_env[key] = os.environ.get(key)
        os.environ[key] = value


def tearDownModule():
    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)


def sh(args, cwd, env=None, input=None):
    full_env = dict(os.environ)
    full_env.update(env or {})
    proc = subprocess.run(["git"] + args, cwd=cwd, env=full_env, input=input,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError("git %s failed: %s"
                             % (args, proc.stderr.decode()))
    return proc.stdout.decode("utf-8", "replace").strip()


class Repo:
    def __init__(self, path):
        self.path = path
        os.makedirs(path)
        sh(["init", "-q", "-b", "main"], path)

    def commit(self, msg, files, when, tz="+0000", author_when=None):
        for name, content in files.items():
            full = os.path.join(self.path, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(content)
            sh(["add", name], self.path)
        env = {"GIT_AUTHOR_DATE": "@%d %s" % (author_when or when, tz),
               "GIT_COMMITTER_DATE": "@%d %s" % (when, tz)}
        sh(["commit", "-q", "--allow-empty", "-m", msg], self.path, env)
        return sh(["rev-parse", "HEAD"], self.path)

    def git(self, *args, **kw):
        return sh(list(args), self.path, **kw)


class BraidTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="braid-test-")
        self.out = os.path.join(self.tmp, "mono")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def repo(self, name):
        return Repo(os.path.join(self.tmp, name))

    def braid(self, *args, expect_ok=True):
        proc = subprocess.run(
            [sys.executable, SCRIPT] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        if expect_ok and proc.returncode != 0:
            self.fail("qdvc-repo-braid failed:\n" + err)
        if not expect_ok and proc.returncode == 0:
            self.fail("qdvc-repo-braid unexpectedly succeeded:\n" + err)
        return out, err

    def mono(self, *args):
        return sh(list(args), self.out)

    def two_simple(self):
        mac, gtk = self.repo("mac-src"), self.repo("gtk-src")
        mac.commit("mac 1", {"a.swift": "1"}, 1000)
        gtk.commit("gtk 1", {"main.py": "1"}, 2000, tz="+0530")
        mac.commit("mac 2", {"a.swift": "2"}, 3000)
        gtk.commit("gtk 2", {"main.py": "2"}, 4000, tz="+0530")
        mac.commit("mac 3", {"b.swift": "3"}, 5000)
        gtk.commit("gtk 3", {"sub/x.py": "3"}, 6000, tz="+0530")
        return mac, gtk


class TestBasics(BraidTestCase):
    def test_interleaves_and_moves_into_folders(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)

        subjects = self.mono("log", "--reverse", "--format=%s").splitlines()
        self.assertEqual(subjects, ["mac 1", "gtk 1", "mac 2", "gtk 2",
                                    "mac 3", "gtk 3"])
        self.assertEqual(self.mono("rev-parse", "main:mac"),
                         mac.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(self.mono("rev-parse", "main:gtk4"),
                         gtk.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(sorted(os.listdir(self.out)),
                         [".git", "gtk4", "mac"])
        with open(os.path.join(self.out, "gtk4", "sub", "x.py")) as fh:
            self.assertEqual(fh.read(), "3")
        self.mono("fsck", "--strict", "--no-dangling")
        self.assertEqual(self.mono("status", "--porcelain"), "")

    def test_each_commit_only_touches_its_folder(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        for line in self.mono("log", "--format=%H %s").splitlines():
            oid, subject = line.split(" ", 1)
            folder = "mac/" if subject.startswith("mac") else "gtk4/"
            files = self.mono("show", "--format=", "--name-only", oid)
            for f in files.splitlines():
                self.assertTrue(f.startswith(folder), (subject, f))

    def test_preserves_identities_dates_and_timezones(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        fmt = "--format=%an|%ae|%ad|%cn|%ce|%cd|%B"
        orig = set(mac.git("log", fmt, "--date=raw").split("\n\n"))
        orig |= set(gtk.git("log", fmt, "--date=raw").split("\n\n"))
        new = set(self.mono("log", fmt, "--date=raw").split("\n\n"))
        self.assertEqual(orig, new)
        self.assertIn("+0530", self.mono("log", "-1", "--format=%ad",
                                         "--date=raw"))

    def test_blame_and_path_log_work(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        self.assertEqual(
            self.mono("log", "--format=%s", "--", "mac/a.swift").splitlines(),
            ["mac 2", "mac 1"])
        self.assertIn("2", self.mono("blame", "--porcelain", "mac/a.swift"))

    def test_per_repo_order_kept_when_dates_go_backwards(self):
        mac, gtk = self.repo("m"), self.repo("g")
        mac.commit("m1", {"f": "1"}, 1000)
        mac.commit("m2", {"f": "2"}, 500)      # clock went backwards
        mac.commit("m3", {"f": "3"}, 3000)
        gtk.commit("g1", {"f": "1"}, 800)
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        subjects = self.mono("log", "--reverse", "--format=%s").splitlines()
        self.assertEqual(subjects, ["g1", "m1", "m2", "m3"])

    def test_order_by_author(self):
        mac, gtk = self.repo("m"), self.repo("g")
        mac.commit("m1", {"f": "1"}, 5000, author_when=1000)
        gtk.commit("g1", {"f": "1"}, 2000, author_when=2000)
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--order-by", "author")
        self.assertEqual(self.mono("log", "--reverse", "--format=%s")
                         .splitlines(), ["m1", "g1"])

    def test_three_sources_and_nested_folders(self):
        a, b, c = self.repo("a"), self.repo("b"), self.repo("c")
        a.commit("a1", {"f": "a"}, 100)
        b.commit("b1", {"f": "b"}, 200)
        c.commit("c1", {"f": "c"}, 300)
        a.commit("a2", {"f": "a2"}, 400)
        self.braid("apps/mac=" + a.path, "apps/gtk4=" + b.path,
                   "shared=" + c.path, "-o", self.out)
        self.assertEqual(self.mono("log", "--reverse", "--format=%s")
                         .splitlines(), ["a1", "b1", "c1", "a2"])
        with open(os.path.join(self.out, "apps", "mac", "f")) as fh:
            self.assertEqual(fh.read(), "a2")
        self.mono("fsck", "--strict", "--no-dangling")

    def test_branch_option_and_branch_name(self):
        mac, gtk = self.two_simple()
        mac.git("checkout", "-q", "-b", "develop")
        mac.commit("mac dev", {"d": "1"}, 7000)
        mac.git("checkout", "-q", "main")
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--branch", "mac=develop", "--branch-name", "trunk")
        self.assertEqual(self.mono("symbolic-ref", "HEAD"), "refs/heads/trunk")
        self.assertEqual(self.mono("log", "-1", "--format=%s"), "mac dev")

    def test_message_options(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--subject-prefix", "[{folder}] ", "--trailer")
        msg = self.mono("log", "-1", "--format=%B")
        self.assertTrue(msg.startswith("[gtk4] gtk 3"))
        orig = gtk.git("rev-parse", "HEAD")
        self.assertIn("Braided-From: gtk4 " + orig, msg)
        self.assertEqual(
            self.mono("log", "-1", "--format=%(trailers:key=Braided-From,"
                      "valueonly)"), "gtk4 " + orig)

    def test_commit_map(self):
        mac, gtk = self.two_simple()
        map_file = os.path.join(self.tmp, "map.tsv")
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--map-file", map_file)
        with open(map_file) as fh:
            rows = [l.split("\t") for l in fh.read().splitlines()[1:]]
        self.assertEqual(len(rows), 6)
        for folder, old, new in rows:
            src = mac if folder == "mac" else gtk
            self.assertEqual(src.git("log", "-1", "--format=%s", old),
                             self.mono("log", "-1", "--format=%s", new))

    def test_default_map_file_in_git_dir(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        self.assertTrue(os.path.exists(
            os.path.join(self.out, ".git", "repo-braid-commit-map.tsv")))

    def test_import_refs_removed_and_originals_pruned(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        self.assertEqual(self.mono("for-each-ref", "refs/repo-braid-import/"), "")
        orig = mac.git("rev-parse", "HEAD")
        proc = subprocess.run(["git", "cat-file", "-e", orig], cwd=self.out)
        self.assertNotEqual(proc.returncode, 0)

    def test_keep_import_refs(self):
        mac, gtk = self.two_simple()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--keep-import-refs")
        self.assertIn("refs/repo-braid-import/0/head",
                      self.mono("for-each-ref", "--format=%(refname)"))

    def test_dry_run_creates_nothing(self):
        mac, gtk = self.two_simple()
        out, _ = self.braid("mac=" + mac.path, "gtk4=" + gtk.path,
                            "-o", self.out, "--dry-run")
        self.assertFalse(os.path.exists(self.out))
        lines = out.splitlines()
        self.assertEqual(len(lines), 6)
        self.assertIn("mac", lines[0])
        self.assertIn("gtk 1", lines[1])
        self.assertTrue(lines[0].startswith("1970-01-01 00:16"))

    def test_empty_initial_commit(self):
        mac, gtk = self.repo("m"), self.repo("g")
        mac.commit("empty start", {}, 100)
        mac.commit("m1", {"f": "1"}, 300)
        gtk.commit("g1", {"f": "1"}, 200)
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        first = self.mono("rev-list", "--max-parents=0", "HEAD")
        self.assertEqual(self.mono("ls-tree", first), "")
        self.mono("fsck", "--strict", "--no-dangling")

    def test_non_utf8_commit_encoding_preserved(self):
        mac, gtk = self.two_simple()
        mac.git("config", "i18n.commitEncoding", "ISO-8859-1")
        with open(os.path.join(mac.path, "l"), "w") as fh:
            fh.write("x")
        mac.git("add", "l")
        msg_file = os.path.join(self.tmp, "msg")
        with open(msg_file, "wb") as fh:
            fh.write("café latin1\n".encode("latin-1"))
        mac.git("commit", "-q", "-F", msg_file,
                env={"GIT_COMMITTER_DATE": "@9000 +0000",
                     "GIT_AUTHOR_DATE": "@9000 +0000"})
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--subject-prefix", "[{folder}] ")
        raw = subprocess.run(["git", "cat-file", "commit", "HEAD"],
                             cwd=self.out, stdout=subprocess.PIPE).stdout
        self.assertIn(b"encoding ISO-8859-1", raw)
        self.assertIn(b"[mac] caf\xe9 latin1", raw)
        self.assertEqual(self.mono("log", "-1", "--format=%s"),
                         "[mac] café latin1")


class TestMerges(BraidTestCase):
    def build(self):
        mac, gtk = self.repo("mac-src"), self.repo("gtk-src")
        mac.commit("m1", {"f": "1"}, 1000)
        gtk.commit("g1", {"f": "1"}, 1500)
        mac.git("checkout", "-q", "-b", "feature")
        mac.commit("feat a", {"feat": "a"}, 2000)
        gtk.commit("g2", {"f": "2"}, 2500)
        mac.commit("feat b", {"feat": "b"}, 3000)
        mac.git("checkout", "-q", "main")
        mac.commit("m2", {"f": "2"}, 3500)
        mac.git("merge", "-q", "--no-ff", "feature", "-m", "merge feature",
                env={"GIT_COMMITTER_DATE": "@4000 +0000",
                     "GIT_AUTHOR_DATE": "@4000 +0000"})
        gtk.commit("g3", {"f": "3"}, 4500)
        return mac, gtk

    def test_topology_preserved(self):
        mac, gtk = self.build()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        self.assertEqual(int(self.mono("rev-list", "--count", "HEAD")), 8)
        merge = self.mono("log", "--merges", "--format=%H")
        self.assertEqual(len(merge.splitlines()), 1)
        self.assertEqual(len(self.mono("log", "-1", "--format=%P", merge)
                             .split()), 2)
        first_parent = self.mono("log", "--first-parent", "--reverse",
                                 "--format=%s").splitlines()
        self.assertEqual(first_parent, ["m1", "g1", "g2", "m2",
                                        "merge feature", "g3"])
        self.mono("fsck", "--strict", "--no-dangling")

    def test_side_branch_keeps_other_folder_from_fork_point(self):
        mac, gtk = self.build()
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        feat_b = self.mono("log", "--all", "--format=%H", "--grep",
                           "^feat b$")
        # The feature branch forked from m1, which was braided before g1, so
        # it has no gtk4/ folder at all, even though g1 and g2 happened (by
        # date) before feat b. Side branches only ever change their own
        # folder; the merge brings the two lines together.
        self.assertEqual(self.mono("ls-tree", "--name-only", feat_b), "mac")
        self.assertEqual(self.mono("show", feat_b + ":mac/feat"), "b")
        files = self.mono("show", "--format=", "--name-only", feat_b)
        self.assertEqual(files, "mac/feat")

    def test_side_branch_sees_other_folder_as_of_fork(self):
        mac, gtk = self.repo("m"), self.repo("g")
        mac.commit("m1", {"f": "1"}, 100)
        gtk.commit("g1", {"f": "1"}, 150)
        mac.commit("m2", {"f": "2"}, 200)          # braided after g1
        mac.git("checkout", "-q", "-b", "feature")
        gtk.commit("g2", {"f": "2"}, 250)
        mac.commit("feat", {"x": "1"}, 300)
        mac.git("checkout", "-q", "main")
        mac.git("merge", "-q", "--no-ff", "feature", "-m", "merge",
                env={"GIT_COMMITTER_DATE": "@400 +0000",
                     "GIT_AUTHOR_DATE": "@400 +0000"})
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        feat = self.mono("log", "--all", "--format=%H", "--grep", "^feat$")
        self.assertEqual(self.mono("show", feat + ":gtk4/f"), "1")
        self.assertEqual(self.mono("show", "HEAD:gtk4/f"), "2")
        self.assertEqual(self.mono("show", "--format=", "--name-only", feat),
                         "mac/x")

    def test_linear_drops_side_commits(self):
        mac, gtk = self.build()
        _, err = self.braid("mac=" + mac.path, "gtk4=" + gtk.path,
                            "-o", self.out, "--linear")
        self.assertEqual(int(self.mono("rev-list", "--count", "HEAD")), 6)
        self.assertEqual(self.mono("log", "--merges", "--format=%H"), "")
        self.assertEqual(self.mono("show", "HEAD:mac/feat"), "b")

    def test_merge_of_unrelated_history_inside_a_source(self):
        mac, gtk = self.repo("m"), self.repo("g")
        mac.commit("m1", {"f": "1"}, 100)
        gtk.commit("g1", {"f": "1"}, 150)
        mac.git("checkout", "-q", "--orphan", "other")
        mac.git("rm", "-q", "-rf", ".")
        mac.commit("orphan", {"o": "1"}, 200)
        mac.git("checkout", "-q", "main")
        mac.git("merge", "-q", "--allow-unrelated-histories", "other",
                "-m", "join", env={"GIT_COMMITTER_DATE": "@300 +0000",
                                   "GIT_AUTHOR_DATE": "@300 +0000"})
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        orphan = self.mono("log", "--all", "--format=%H", "--grep",
                           "^orphan$")
        self.assertEqual(self.mono("ls-tree", "--name-only", orphan), "mac")
        self.assertEqual(self.mono("show", "HEAD:mac/o"), "1")
        self.mono("fsck", "--strict", "--no-dangling")


class TestTags(BraidTestCase):
    def test_lightweight_and_annotated(self):
        mac, gtk = self.two_simple()
        mac.git("tag", "v1.0", "HEAD~1")
        gtk.git("tag", "-a", "v2.0", "-m", "release 2",
                env={"GIT_COMMITTER_DATE": "@7000 +0000"})
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out)
        self.assertEqual(self.mono("log", "-1", "--format=%s", "mac/v1.0"),
                         "mac 2")
        self.assertEqual(self.mono("cat-file", "-t", "gtk4/v2.0"), "tag")
        self.assertEqual(self.mono("log", "-1", "--format=%s", "gtk4/v2.0"),
                         "gtk 3")
        raw = self.mono("cat-file", "tag", "gtk4/v2.0")
        self.assertIn("tag gtk4/v2.0", raw)
        self.assertIn("release 2", raw)
        self.mono("fsck", "--strict", "--no-dangling")

    def test_tag_prefix_and_no_tags(self):
        mac, gtk = self.two_simple()
        mac.git("tag", "v1.0")
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--tag-prefix", "{folder}-")
        self.assertEqual(self.mono("tag"), "mac-v1.0")
        shutil.rmtree(self.out)
        self.braid("mac=" + mac.path, "gtk4=" + gtk.path, "-o", self.out,
                   "--no-tags")
        self.assertEqual(self.mono("tag"), "")

    def test_tag_on_unimported_branch_is_skipped(self):
        mac, gtk = self.two_simple()
        mac.git("checkout", "-q", "-b", "elsewhere")
        mac.commit("elsewhere", {"e": "1"}, 9000)
        mac.git("tag", "only-elsewhere")
        mac.git("checkout", "-q", "main")
        _, err = self.braid("mac=" + mac.path, "gtk4=" + gtk.path,
                            "-o", self.out)
        self.assertEqual(self.mono("tag"), "")
        self.assertIn("1 tags were skipped", err)


class TestValidation(BraidTestCase):
    def test_case_insensitive_collision(self):
        mac, gtk = self.two_simple()
        _, err = self.braid("Mac=" + mac.path, "mac=" + gtk.path,
                            "-o", self.out, expect_ok=False)
        self.assertIn("case-insensitive", err)

    def test_overlapping_folders(self):
        mac, gtk = self.two_simple()
        _, err = self.braid("apps=" + mac.path, "apps/gtk=" + gtk.path,
                            "-o", self.out, expect_ok=False)
        self.assertIn("overlap", err)

    def test_bad_folder_names(self):
        for bad in ("..", "a/../b", ".git", "x/.GIT", ""):
            with self.assertRaises(gb.BraidError, msg=bad):
                gb.normalise_prefix(bad)
        self.assertEqual(gb.normalise_prefix("/apps\\mac/"), "apps/mac")

    def test_nonempty_output_refused(self):
        mac, gtk = self.two_simple()
        os.makedirs(self.out)
        with open(os.path.join(self.out, "keep.txt"), "w") as fh:
            fh.write("precious")
        _, err = self.braid("mac=" + mac.path, "gtk4=" + gtk.path,
                            "-o", self.out, expect_ok=False)
        self.assertIn("not empty", err)
        self.assertNotIn("should be deleted", err)
        self.assertEqual(os.listdir(self.out), ["keep.txt"])

    def test_shallow_clone_refused(self):
        mac, gtk = self.two_simple()
        shallow = os.path.join(self.tmp, "shallow")
        sh(["clone", "-q", "--depth", "1", "file://" + mac.path, shallow],
           self.tmp)
        _, err = self.braid("mac=" + shallow, "gtk4=" + gtk.path,
                            "-o", self.out, expect_ok=False)
        self.assertIn("shallow", err)

    def test_not_a_repo(self):
        mac, _ = self.two_simple()
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        _, err = self.braid("mac=" + mac.path, "gtk4=" + plain,
                            "-o", self.out, expect_ok=False)
        self.assertIn("not a Git repository", err)

    def test_root_file_warnings(self):
        mac, gtk = self.repo("m"), self.repo("g")
        mac.commit("m1", {".github/workflows/ci.yml": "x",
                          ".gitattributes": "*.png filter=lfs diff=lfs\n",
                          ".gitmodules": "[submodule]\n"}, 100)
        gtk.commit("g1", {"f": "1"}, 200)
        _, err = self.braid("mac=" + mac.path, "gtk4=" + gtk.path,
                            "-o", self.out)
        self.assertIn("submodules", err)
        self.assertIn("ignored by GitHub", err)
        self.assertIn("Git LFS", err)

    def test_shared_history_warning(self):
        mac, _ = self.two_simple()
        fork = os.path.join(self.tmp, "fork")
        sh(["clone", "-q", mac.path, fork], self.tmp)
        sh(["commit", "-q", "--allow-empty", "-m", "fork"], fork,
           env={"GIT_COMMITTER_DATE": "@8000 +0000",
                "GIT_AUTHOR_DATE": "@8000 +0000"})
        _, err = self.braid("mac=" + mac.path, "gtk4=" + fork,
                            "-o", self.out)
        self.assertIn("share 3 commits", err)
        self.assertEqual(int(self.mono("rev-list", "--count", "HEAD")), 7)


class TestUnits(unittest.TestCase):
    def test_signature_headers_dropped(self):
        raw = (b"tree " + b"a" * 40 + b"\n"
               b"parent " + b"b" * 40 + b"\n"
               b"author A <a@x> 1 +0000\n"
               b"committer A <a@x> 1 +0000\n"
               b"gpgsig -----BEGIN SSH SIGNATURE-----\n"
               b" abc\n"
               b" \n"
               b" -----END SSH SIGNATURE-----\n"
               b"\nmessage\n\nbody\n")
        c = gb.Commit("c" * 40, raw)
        self.assertEqual(c.parents, ["b" * 40])
        self.assertEqual(c.message, b"message\n\nbody\n")
        new = gb.build_commit(c, "d" * 40, ["e" * 40, "f" * 40], c.message)
        self.assertNotIn(b"gpgsig", new)
        self.assertNotIn(b"SIGNATURE", new)
        self.assertTrue(new.startswith(b"tree " + b"d" * 40 + b"\nparent "
                                       + b"e" * 40 + b"\nparent "))
        self.assertTrue(new.endswith(b"\n\nmessage\n\nbody\n"))

    def test_strip_tag_signature(self):
        msg = (b"release\n-----BEGIN PGP SIGNATURE-----\nxyz\n"
               b"-----END PGP SIGNATURE-----\n")
        self.assertEqual(gb.strip_signature(msg), b"release\n")
        self.assertEqual(gb.strip_signature(b"plain\n"), b"plain\n")

    def test_add_trailer(self):
        self.assertEqual(gb.add_trailer(b"subj\n", b"K: v"),
                         b"subj\n\nK: v\n")
        self.assertEqual(gb.add_trailer(b"subj\n\nSigned-off-by: x\n",
                                        b"K: v"),
                         b"subj\n\nSigned-off-by: x\nK: v\n")
        self.assertEqual(gb.add_trailer(b"", b"K: v"), b"K: v\n")

    def test_format_time_before_1970(self):
        self.assertEqual(gb.format_time(-86400), "1969-12-31 00:00")


if __name__ == "__main__":
    unittest.main()

"""merge.py on a temporary data folder, collect_browser.py helpers, readme_tables.py, and every script's CLI."""
import json
import os
import subprocess
import sys
import unittest

from helpers import COLLECTOR, TOOLS, TempDir, read, record

import collect_browser
import emcdata
import merge
import readme_tables


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.dir = self.tmp.__enter__()
        self.data = os.path.join(self.dir, "data")
        self.old_dir, emcdata.DIR = emcdata.DIR, self.data
        merge.DRY_RUN = False
        emcdata.save(record("one.example", 1, status="checked"), self.data)

    def tearDown(self):
        emcdata.DIR = self.old_dir
        merge.DRY_RUN = False
        self.tmp.__exit__(None, None, None)

    def test_add(self):
        work = os.path.join(self.dir, "work")
        emcdata.save(record("two.example", 1, status="checked", dead=True), os.path.join(work, "records"))
        emcdata.save(record("one.example", 1), os.path.join(work, "records"))
        merge.cmd_add(work)
        two = emcdata.load("two.example")
        self.assertEqual((two["order"], two["status"], two.get("dead")), (2, "pending", None))
        self.assertEqual(emcdata.load("one.example")["status"], "checked")  # existing providers are skipped
        self.assertIn("Skipped (already in the data)", read(os.path.join(work, "review-add.md")))

    def test_add_without_drafts(self):
        with self.assertRaises(SystemExit):
            merge.cmd_add(os.path.join(self.dir, "empty"))

    def test_set_and_remove(self):
        p = os.path.join(self.dir, "set.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"set": {"ONE.example": {"owner": "🇳🇴 X AS", "links.tos": "https://one.example/t"},
                               "missing.example": {"owner": "x"}}}, f)
        merge.cmd_set(p)
        rec = emcdata.load("one.example")
        self.assertEqual((rec["owner"], rec["links"]["tos"]), ("🇳🇴 X AS", "https://one.example/t"))
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"set": {"one.example": {"owner": None, "links.tos": None}}}, f)
        merge.cmd_set(p)
        rec = emcdata.load("one.example")
        self.assertNotIn("owner", rec)
        self.assertNotIn("links", rec)

    def test_dry_run_writes_nothing(self):
        p = os.path.join(self.dir, "set.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"set": {"one.example": {"owner": "Y"}}}, f)
        merge.DRY_RUN = True
        merge.cmd_set(p)
        self.assertNotIn("owner", emcdata.load("one.example"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "set-review.md")))

    def test_unknown_field_is_refused_before_writing(self):
        p = os.path.join(self.dir, "set.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"set": {"one.example": {"colour": "red"}}}, f)
        with self.assertRaises(ValueError):
            merge.cmd_set(p)
        self.assertNotIn("colour", read(emcdata.path("one.example")))

    def test_security_and_servers(self):
        deep = {"spf": "v=spf1 ip4:192.0.2.1 ~all", "dmarc": "v=DMARC1; p=quarantine", "mta_sts": "",
                "tls_rpt": "", "dnssec": False, "dane": False,
                "autoconfig": {"servers": [{"type": "imap", "host": "mx.one.example", "port": "993", "socket": "SSL"}]},
                "imap": [{"host": "mx.one.example", "banner": "* OK Stalwart", "software": "Stalwart"}]}
        self.assertEqual(merge.security("one.example", {"mx": [[10, "mx.one.example"]]}, deep),
                         {"spf": "~all", "dmarc": "quarantine", "mta_sts": "missing", "tls_rpt": "missing",
                          "dnssec": "no", "dane": "no"})
        self.assertEqual(merge.servers(deep), [{"kind": "IMAP", "host": "mx.one.example:993", "note": "SSL/TLS"},
                                               {"kind": "IMAP software", "host": "Stalwart"}])


class BrowserHelpers(unittest.TestCase):
    def test_code_links(self):
        links = [("https://github.com/FoldoyMail/foldoy-mail", "Source"), ("https://github.com/roundcube/x", ""),
                 ("https://github.com/someone/else", "")]
        mine, other = collect_browser.code_links(links, "foldoy.com")
        self.assertEqual(mine, ["https://github.com/FoldoyMail/foldoy-mail"])
        self.assertEqual(other, ["https://github.com/someone/else"])

    def test_social_links(self):
        links = [("https://twitter.com/examplemail", ""), ("https://twitter.com/share", ""),
                 ("https://matrix.to/#/#room:example.org", ""), ("https://example.org/about", "")]
        out = {s["net"]: s for s in collect_browser.social_links(links, [], "examplemail.org")}
        self.assertEqual(set(out), {"X", "Matrix"})
        self.assertTrue(out["X"]["match"])

    def test_missing_browser(self):
        with self.assertRaises(SystemExit):
            collect_browser.find_browser("/nonexistent/chrome")


class ReadmeTables(unittest.TestCase):
    def test_width_and_align(self):
        self.assertEqual(readme_tables.width("🇳🇴"), 2)
        self.assertEqual(readme_tables.width("✅"), 2)
        rows = readme_tables.align(["| a | bb |", "|---|---|", "| ✅ | c |"])
        self.assertEqual(rows, ["| a   | bb  |", "|-----|-----|", "| ✅  | c   |"])


class Cli(unittest.TestCase):
    SCRIPTS = [os.path.join(COLLECTOR, n) for n in ("collect.py", "collect_dns.py", "collect_web.py",
                                                    "collect_deep.py", "build_csv.py", "collect_browser.py",
                                                    "merge.py", "run_deep_refresh.py")] + \
              [os.path.join(TOOLS, n) for n in ("fetch_favicons.py", "readme_tables.py", "test_data.py")]

    def run_script(self, *args):
        return subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True, timeout=120)

    def test_help(self):
        for script in self.SCRIPTS:
            r = self.run_script(script, "--help")
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr}")
            self.assertIn("usage", r.stdout.lower(), script)

    def test_bad_domain_is_refused(self):
        with TempDir() as d:
            r = self.run_script(os.path.join(COLLECTOR, "collect.py"), "--work", d, "../etc")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a domain name", r.stderr)

    def test_favicons_need_a_target(self):
        r = self.run_script(os.path.join(TOOLS, "fetch_favicons.py"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()

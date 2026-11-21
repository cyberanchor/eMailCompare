import os
import unittest
import urllib.robotparser

from helpers import TempDir, read

import common


class Domains(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(common.normalize_domain(" Example.COM. "), "example.com")
        self.assertEqual(common.normalize_domain("https://Mail.Example.org:443/signup"), "mail.example.org")
        self.assertEqual(common.normalize_domain("bücher.de"), "xn--bcher-kva.de")

    def test_refused(self):
        for bad in ("../etc", "a b.com", "localhost", "192.0.2.1", "-a.com", "a_b.com", "", "com"):
            with self.assertRaises(ValueError, msg=bad):
                common.normalize_domain(bad)

    def test_domains_tsv(self):
        with TempDir() as d:
            p = os.path.join(d, "domains.tsv")
            with open(p, "w", encoding="utf-8") as f:
                f.write("Example.org\t1\n\nexample.net\t0\n")
            self.assertEqual(common.read_domains_tsv(p), [("example.org", True), ("example.net", False)])
            with open(p, "w", encoding="utf-8") as f:
                f.write("../x\t0\n")
            with self.assertRaises(ValueError):
                common.read_domains_tsv(p)


class Files(unittest.TestCase):
    def test_json(self):
        with TempDir() as d:
            p = os.path.join(d, "sub", "x.json")
            self.assertEqual(common.read_json(p), {})
            self.assertEqual(common.read_json(p, []), [])
            common.write_json(p, {"ä": [1, 2]})
            self.assertEqual(common.read_json(p), {"ä": [1, 2]})
            self.assertEqual(os.listdir(os.path.dirname(p)), ["x.json"])

    def test_rewrite_keeps_mode(self):
        with TempDir() as d:
            p = os.path.join(d, "x.txt")
            common.write_text(p, "a")
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o666 & ~common._UMASK)
            os.chmod(p, 0o600)
            common.write_bytes(p, b"b")
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            self.assertEqual(read(p, "rb"), b"b")


class Robots(unittest.TestCase):
    def robots(self, lines):
        r = common.Robots()
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(lines)
        r.cache["https://example.org"] = rp
        return r

    def test_rules(self):
        r = self.robots(["User-agent: *", "Disallow: /account/", "", "User-agent: eMailCompare-research",
                         "Disallow: /private/"])
        self.assertTrue(r.allowed("https://example.org/terms"))
        self.assertFalse(r.allowed("https://example.org/private/x"))

    def test_disallow_all(self):
        r = self.robots(["User-agent: *", "Disallow: /"])
        self.assertFalse(r.allowed("https://example.org/"))

    def test_non_http_is_not_checked(self):
        self.assertTrue(common.Robots().allowed("mailto:a@example.org"))

    def test_user_agent_names_the_project(self):
        self.assertIn("eMailCompare", common.USER_AGENT)
        self.assertIn("+https://", common.USER_AGENT)


if __name__ == "__main__":
    unittest.main()

import os
import unittest

from helpers import TempDir, read, record

import emcdata


class RoundTrip(unittest.TestCase):
    def test_save_and_load(self):
        rec = record(created="2013-08-29", email="yes", phone="no", price=["free", "1.50 EUR/month"],
                     alliance=14, links={"tos": "https://example.org/tos"},
                     a=[{"host": "192.0.2.1", "flag": "🇩🇪", "country": "Germany", "asn": "AS1", "org": "X"}],
                     note="two\nlines", security={"spf": "-all", "dnssec": "yes"})
        with TempDir() as d:
            emcdata.save(rec, d)
            back = emcdata.load_all(d)[0]
            back.pop("_file")
            self.assertEqual(back, rec)
            text = read(os.path.join(d, "example.org.yaml"))
        self.assertIn("created: '2013-08-29'", text)   # dates stay text for YAML 1.2 readers
        self.assertIn("email: 'yes'", text)            # so do yes / no
        self.assertIn("- {host: '192.0.2.1',", text)   # A/MX records on one line

    def test_loader_reads_like_yaml_1_2(self):
        data = emcdata.loads("a: yes\nb: 2024-01-01\nc: 1_000\nd: on\ne: true\nf: 0x10\n")
        self.assertEqual(data, {"a": "yes", "b": "2024-01-01", "c": "1_000", "d": "on", "e": True, "f": 16})

    def test_empty_values_left_out(self):
        text = emcdata.dumps(record(dead=False, note="", social=[], links={"tos": ""}))
        for key in ("dead", "note", "social", "links"):
            self.assertNotIn(key + ":", text)

    def test_unknown_fields_are_refused(self):
        with self.assertRaises(ValueError):
            emcdata.dumps(record(colour="red"))
        with self.assertRaises(ValueError):
            emcdata.dumps(record(links={"homepage": "https://example.org/"}))

    def test_file_names_stay_in_the_folder(self):
        for bad in ("../etc/passwd", "a/b.org", "", ".hidden.org", "x..y.org"):
            with self.assertRaises(ValueError, msg=bad):
                emcdata.file_name(bad)
        self.assertEqual(emcdata.file_name("Example.ORG"), "example.org.yaml")

    def test_new_file_mode_follows_umask(self):
        with TempDir() as d:
            emcdata.save(record(), d)
            mode = os.stat(os.path.join(d, "example.org.yaml")).st_mode & 0o777
        self.assertEqual(mode, 0o666 & ~emcdata._UMASK)

    def test_no_temp_files_left(self):
        with TempDir() as d:
            emcdata.save(record(), d)
            self.assertEqual(os.listdir(d), ["example.org.yaml"])

    def test_csv_row_round_trip(self):
        rec = record(flag="🇨🇭", domains=["b.example"], owner="🇨🇭 X AG", jurisdiction=["Switzerland"],
                     price=["free"], security={"spf": "-all", "dmarc": "reject"},
                     servers=[{"kind": "IMAP", "host": "imap.example.org:993", "note": "SSL/TLS"}],
                     links={"tos": "https://example.org/t", "imprint": "https://example.org/i"})
        row = emcdata.to_row(rec)
        self.assertEqual(len(row), len(emcdata.HEADER))
        back = emcdata.from_row(row, "pending", False, 1)
        self.assertEqual(emcdata.dumps(back), emcdata.dumps(rec))


if __name__ == "__main__":
    unittest.main()

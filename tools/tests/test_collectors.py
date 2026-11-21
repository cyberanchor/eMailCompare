"""Parsers of collect_dns.py, collect_web.py, collect_deep.py and build_csv.py, on fixed inputs (no network)."""
import os
import unittest
import urllib.robotparser

from helpers import TempDir

import build_csv
import collect_deep
import collect_dns
import collect_web

PSL = """// comment
// ===BEGIN ICANN DOMAINS===
com
org
uk
co.uk
*.ck
!www.ck
// ===END ICANN DOMAINS===
// ===BEGIN PRIVATE DOMAINS===
github.io
// ===END PRIVATE DOMAINS===
"""


class Dns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TempDir() as d:
            p = os.path.join(d, "psl.dat")
            with open(p, "w", encoding="utf-8") as f:
                f.write(PSL)
            collect_dns.load_psl(p)

    def test_registrable(self):
        r = collect_dns.registrable
        self.assertEqual(r("mail.example.co.uk"), "example.co.uk")
        self.assertEqual(r("a.b.example.com"), "example.com")
        self.assertEqual(r("foo.bar.ck"), "foo.bar.ck")
        self.assertEqual(r("www.ck"), "www.ck")
        self.assertEqual(r("user.github.io"), "github.io")  # private part of the list is not used

    def test_bad_psl(self):
        with TempDir() as d:
            p = os.path.join(d, "psl.dat")
            with open(p, "w", encoding="utf-8") as f:
                f.write("nothing here\n")
            with self.assertRaises(ValueError):
                collect_dns.load_psl(p)
        self.setUpClass()

    def test_dates(self):
        n = collect_dns.norm_date
        for text in ("2013-08-29T10:00:00Z", "29.08.2013", "29-Aug-2013", "August 29, 2013", "20130829"):
            self.assertEqual(n(text), "2013-08-29", text)
        self.assertIsNone(n("no date"))
        self.assertIsNone(n("2013-13-40"))

    def test_whois(self):
        txt = ("Domain Name: EXAMPLE.ORG\nCreation Date: 2001-02-03T04:05:06Z\n"
               "Registry Expiry Date: 2030-02-03T04:05:06Z\nRegistrant Organization: REDACTED FOR PRIVACY\n"
               "Registrant Country: NO\n")
        self.assertEqual(collect_dns.parse_whois(txt),
                         {"created": "2001-02-03", "expires": "2030-02-03", "registrant_cc": "NO"})

    def test_rdap(self):
        data = {"events": [{"eventAction": "registration", "eventDate": "2026-08-10T12:00:00Z"},
                           {"eventAction": "expiration", "eventDate": "2027-08-10T12:00:00Z"}],
                "status": ["active"],
                "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Reg Inc"]]]},
                             {"roles": ["registrant"], "vcardArray": ["vcard", [
                                 ["org", {}, "text", "Example AS"], ["adr", {"cc": "no"}, "text", ""]]]}]}
        out = collect_dns.parse_rdap(data)
        self.assertEqual((out["created"], out["expires"]), ("2026-08-10", "2027-08-10"))
        self.assertEqual((out["registrar"], out["registrant_org"], out["registrant_cc"]),
                         ("Reg Inc", "Example AS", "NO"))

    def test_mx(self):
        ans = [("MX", "20 mx2.example.org."), ("MX", "10 MX1.example.org."), ("A", "192.0.2.1")]
        self.assertEqual(collect_dns.parse_mx(ans), [(10, "mx1.example.org"), (20, "mx2.example.org")])


HOME = """<html><head><title> Example Mail </title><meta name="description" content="Private e-mail">
</head><body><nav><a href="/terms">Terms of service</a> <a href="/privacy-policy">Privacy</a>
<a href="https://twitter.com/example">X</a><a href="/pricing">Plans</a>
<a href="https://other.example.net/signup">Sign up</a></nav>
<script>var x = 1;</script><p>Write  freely.</p><form><input type="password"></form></body></html>"""


class Web(unittest.TestCase):
    def test_page(self):
        p = collect_web.parse(HOME)
        self.assertEqual(p.title.strip(), "Example Mail")
        self.assertEqual(p.meta["description"], "Private e-mail")
        self.assertTrue(p.password)
        self.assertIn("Write freely.", p.text())
        self.assertNotIn("var x", p.text())

    def test_pick_links(self):
        p = collect_web.parse(HOME)
        links = collect_web.pick_links(p.links, "https://example.org/")
        self.assertEqual(links["tos"]["url"], "https://example.org/terms")
        self.assertEqual(links["privacy"]["url"], "https://example.org/privacy-policy")
        self.assertEqual(links["pricing"]["url"], "https://example.org/pricing")
        self.assertNotIn("signup", links)  # sign-up pages only count on the provider's own site

    def test_client_redirect(self):
        html = '<meta http-equiv="refresh" content="0; url=/en/">'
        self.assertEqual(collect_web.client_redirect(html, "https://example.org/"),
                         ("https://example.org/en/", "meta-refresh"))
        js = "<script>window.location.href = 'https://example.net/';</script>"
        self.assertEqual(collect_web.client_redirect(js, "https://example.org/"),
                         ("https://example.net/", "javascript"))

    def test_page_state(self):
        s = collect_web.page_state
        self.assertEqual(s({"status": 200, "html": HOME}), "ok")
        self.assertEqual(s({"status": 403, "html": "<title>Just a moment...</title>"}), "blocked")
        self.assertEqual(s({"status": 200, "html": "<title>example.org</title>This domain is for sale"}), "parked")
        self.assertEqual(s({"status": 500, "html": "<title>Error</title>"}), "http-error")
        self.assertEqual(s({"status": 200, "html": ""}), "unreachable")

    def test_robots_disallow_stops_the_request(self):
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(["User-agent: *", "Disallow: /private/"])
        old = collect_web.ROBOTS
        collect_web.ROBOTS = robots = type(old)()
        robots.cache["https://example.org"] = rp
        try:
            r = collect_web.polite_fetch("https://example.org/private/terms")
        finally:
            collect_web.ROBOTS = old
        self.assertEqual(r["error"], "disallowed by robots.txt")
        self.assertFalse(collect_web.good(r))

    def test_sources_file(self):
        with TempDir() as d:
            p = os.path.join(d, "sources.json")
            self.assertEqual(collect_web.load_sources(p), {})
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"_comment": "x", "Example.org": {"tos": "https://example.org/t"}}')
            self.assertEqual(collect_web.load_sources(p), {"example.org": {"tos": "https://example.org/t"}})


AUTOCONFIG = """<?xml version="1.0"?><clientConfig version="1.1"><emailProvider id="example.org">
<domain>example.org</domain><domain>Example.NET</domain><displayName>Example</displayName>
<incomingServer type="imap"><hostname>imap.example.org</hostname><port>993</port><socketType>SSL</socketType>
</incomingServer><outgoingServer type="smtp"><hostname>smtp.example.org</hostname><port>587</port>
<socketType>STARTTLS</socketType></outgoingServer></emailProvider></clientConfig>"""


class Deep(unittest.TestCase):
    def test_autoconfig(self):
        cfg = collect_deep.parse_autoconfig(AUTOCONFIG)
        self.assertEqual(cfg["domains"], ["example.net", "example.org"])
        self.assertEqual(cfg["servers"][0], {"dir": "in", "type": "imap", "host": "imap.example.org", "port": "993",
                                             "socket": "SSL"})
        self.assertIsNone(collect_deep.parse_autoconfig("<html></html>"))

    def test_imap_software(self):
        self.assertEqual(collect_deep.imap_software("* OK Stalwart IMAP4rev2 at your service."), "Stalwart")
        self.assertEqual(collect_deep.imap_software("* OK [CAPABILITY IMAP4rev1] Dovecot ready."), "Dovecot")
        self.assertEqual(collect_deep.imap_software(""), "")


class Summaries(unittest.TestCase):
    def test_flag_and_alliance(self):
        self.assertEqual(build_csv.flag("no"), "🇳🇴")
        self.assertEqual(build_csv.flag("x"), "")
        self.assertEqual(build_csv.alliance(["NO"]), "Nine Eyes (👀 9)")
        self.assertEqual(build_csv.alliance(["CH", "US"]), "Five Eyes (👀 5)")
        self.assertEqual(build_csv.alliance(["CH"]), "")

    def test_tos(self):
        text = ("These terms apply. " * 20 + "You may not send spam or distribute malware. "
                "Child sexual abuse material is forbidden. Accounts not logged in for 12 months are deleted.")
        out = build_csv.summarize_tos(text)
        self.assertIn("Illegal actions (CSAM, spam, hacking)", out)
        self.assertIn("Delete inactive accounts (12 mos.)", out)
        self.assertEqual(build_csv.summarize_tos("short"), "")

    def test_privacy(self):
        text = ("This policy explains things. " * 15 + "We store your IP address for security. "
                "Logs are deleted after 7 days. We do not use any analytics or tracking scripts on the site. "
                "We never sell your personal data to anyone.")
        out = build_csv.summarize_privacy(text)
        self.assertTrue(out.startswith("Collect IP addresses, data/logs kept 7 days"), out)
        self.assertIn("no tracking (claimed)", out)
        self.assertIn("does not sell data (claimed)", out)

    def test_price(self):
        out = build_csv.summarize_price("Create your free account", "Plus €3/month or €30/year", "", "")
        self.assertEqual(out, "free; €3/month; €30/year")
        self.assertEqual(build_csv.summarize_price("", "Support us", "", "https://example.org/donate"), "donations")

    def test_owner(self):
        home = "Welcome\n© 2024 Example Mail GmbH. All rights reserved."
        self.assertEqual(build_csv.pick_owner(home, "", "", "", []), "Example Mail GmbH")
        self.assertTrue(build_csv.brand_only("Example Mail", "example.org"))

    def test_registrable_of(self):
        self.assertEqual(build_csv.registrable_of("imap.mail.example.co.uk"), "example.co.uk")
        self.assertEqual(build_csv.registrable_of("*.example.org"), "example.org")


if __name__ == "__main__":
    unittest.main()

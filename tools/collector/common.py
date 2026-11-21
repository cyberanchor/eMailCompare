"""Helpers shared by the collector scripts: User-Agent, domain validation, JSON files, robots.txt.

Nothing here touches the network at import time.
"""
import json
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

# Every request names the project and where to read about it, so a site operator can tell who is asking.
# Set EMC_USER_AGENT to use another string (for example one that names your own fork).
USER_AGENT = os.environ.get("EMC_USER_AGENT") or \
    "eMailCompare-research/1.0 (+https://github.com/cyberanchor/eMailCompare)"
ROBOTS_AGENT = "eMailCompare-research"

# A registrable or sub-domain name: letters, digits and hyphens per label, 2 to 127 labels, a letter-only TLD
# (or an xn-- IDN TLD). It is used in file names, so anything else (paths, URLs, spaces) is refused.
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$")


def normalize_domain(value):
    """Lower-cased domain without scheme, path, port or trailing dot; ValueError when it is not a domain name."""
    d = (value or "").strip().lower()
    if "://" in d:
        d = urllib.parse.urlparse(d).hostname or ""
    d = d.split("/")[0].split(":")[0].rstrip(".")
    try:
        d = d.encode("idna").decode("ascii") if any(ord(c) > 127 for c in d) else d
    except UnicodeError:
        raise ValueError(f"not a domain name: {value!r}") from None
    if not DOMAIN_RE.match(d):
        raise ValueError(f"not a domain name: {value!r}")
    return d


def read_domains_tsv(path):
    """[(domain, struck)] from a "domain<TAB>0|1" file; malformed names are refused."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d, _, s = line.rstrip("\n").partition("\t")
            if d.strip():
                out.append((normalize_domain(d), s.strip() == "1"))
    return out


def read_json(path, default=None):
    """Parsed JSON file, or default ({} when not given) when the file does not exist."""
    if not os.path.exists(path):
        return {} if default is None else default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_UMASK = os.umask(0o022)  # read the umask once, at import time (before any thread starts) ...
os.umask(_UMASK)          # ... and put it back


def _file_mode(path):
    """Mode for a rewritten file: the old file's, or what open() would give under the umask (mkstemp alone
    would give rw-------)."""
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return 0o666 & ~_UMASK


def write_text(path, text):
    """Write a file atomically: a reader (or a crash) never sees half of it."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=os.path.basename(path)[-40:])
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, _file_mode(path))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path, obj, indent=1, sort_keys=False):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=sort_keys) + "\n")


def write_bytes(path, data):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, _file_mode(path))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Robots:
    """robots.txt rules per site, fetched once, read as RFC 9309 says: a 4xx answer (no file) allows everything,
    a 5xx answer disallows everything. A network error also allows everything: the page fetch that follows
    fails the same way when the site is down."""

    def __init__(self, agent=ROBOTS_AGENT, user_agent=USER_AGENT, timeout=15):
        self.agent, self.user_agent, self.timeout = agent, user_agent, timeout
        self.cache, self.lock = {}, threading.Lock()

    def _parser(self, root):
        with self.lock:
            if root in self.cache:
                return self.cache[root]
        rp = urllib.robotparser.RobotFileParser()
        req = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except Exception:  # noqa: BLE001  unreachable robots.txt: no rules
            rp.allow_all = True
        with self.lock:
            self.cache[root] = rp
        return rp

    def allowed(self, url):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.netloc:
            return True
        return self._parser(f"{u.scheme}://{u.netloc}").can_fetch(self.agent, url)

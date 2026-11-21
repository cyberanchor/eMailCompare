#!/usr/bin/env python3
"""Provider data of eMailCompare: data/providers/<domain>.yaml, one file per provider.

Shared by the converter, the tests and the collector. The schema is described in data/README.md.

    load_all()            -> list of records, in "order"
    load(domain)          -> one record
    save(record)          -> writes data/providers/<domain>.yaml
    to_row(record)        -> the 28 cells of the old emails.csv (HEADER), used for the CSV export check
    from_row(cells, ...)  -> record built from those 28 cells
"""
import glob
import os
import re
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.environ.get("EMC_DATA_DIR") or os.path.join(ROOT, "data", "providers")

HEADER = [
    "flag", "Domain", "2nd domain", "A Record", "MX Record", "Domain crated", "Domain expired", "Company owner",
    "Intelligence Alliance", "Jurisdiction \n(based on server location)", "Site description", "ToS",
    "Privacy policy", "Email\n(receive a verification link)", "Phone\n(receive s sms code)", "Price",
    "Personal Info\n(first name, last name, adress, etc..)", "Note", "ToS link", "Privacy policy link",
    "Source code", "Social links", "Onion address", "Imprint link", "Mail security", "Mail servers",
    "Online since", "Last checked",
]
ALLIANCE = {5: "Five Eyes (👀 5)", 9: "Nine Eyes (👀 9)", 14: "Fourteen Eyes (👀 14)"}
SECURITY = [("spf", "SPF"), ("dmarc", "DMARC"), ("mta_sts", "MTA-STS"), ("tls_rpt", "TLS-RPT"),
            ("dnssec", "DNSSEC"), ("dane", "DANE")]
STATUSES = ("checked", "auto", "pending")
# key order in the files
KEYS = ["order", "domain", "status", "dead", "flag", "domains", "a", "mx", "created", "expires", "owner",
        "alliance", "jurisdiction", "description", "tos_summary", "privacy_summary", "email", "phone",
        "personal", "price", "note", "links", "social", "onion", "security", "servers", "online_since",
        "last_checked"]
LINKS = ["tos", "privacy", "code", "imprint"]
RECORD = ["host", "flag", "country", "asn", "org"]

# One-line strings written in quotes although PyYAML (YAML 1.1) would leave them plain: everything a YAML 1.2
# parser such as Hugo's might read as a number, boolean or null. Over-quoting is harmless, so any text that starts
# like a number is quoted.
QUOTE = re.compile(r"^(?:[-+]?\.?[0-9]|[-+]?\.(?:inf|Inf|INF|nan|NaN|NAN)$"
                   r"|(?:null|Null|NULL|~|true|True|TRUE|false|False|FALSE)$)")
# Characters that PyYAML treats as line breaks but YAML 1.2 does not: such text is written in double quotes,
# where they become escapes (\N, \L, \P) that both parsers read back exactly.
ODD_BREAKS = ("\x85", "\u2028", "\u2029", "\r")


class _Flow(dict):
    """A mapping written on one line: {host: …, flag: …}."""


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, s):
    if any(c in s for c in ODD_BREAKS):
        return dumper.represent_scalar("tag:yaml.org,2002:str", s, style='"')
    if "\n" in s:
        return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|")
    if QUOTE.match(s):
        return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", s)


_Dumper.add_representer(str, _str)
_Dumper.add_representer(_Flow, lambda d, m: d.represent_mapping("tag:yaml.org,2002:map", m.items(), flow_style=True))
_Dumper.add_representer(dict, lambda d, m: d.represent_mapping("tag:yaml.org,2002:map", m.items()))


_UMASK = os.umask(0o022)  # read once at import time, then put back
os.umask(_UMASK)
SAFE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


def file_name(domain):
    """<domain>.yaml; refuses names that could leave the data folder or are not plain domain names."""
    d = (domain or "").lower()
    if not SAFE_NAME.match(d) or ".." in d:
        raise ValueError(f"not a usable domain name for a file: {domain!r}")
    return d + ".yaml"


def path(domain):
    return os.path.join(DIR, file_name(domain))


def _known(rec, where, keys, allowed):
    unknown = set(keys) - set(allowed)
    if unknown:
        raise ValueError(f"{rec.get('domain')}: unknown {where} fields {sorted(unknown)}")


def dumps(rec):
    """YAML text of a record: keys in KEYS order, empty values left out, A/MX/server lines on one line each.
    Unknown fields, also inside links / security / a / mx / servers, raise ValueError instead of being dropped."""
    _known(rec, "top-level", rec, KEYS)
    for k in ("a", "mx"):
        for r in rec.get(k) or []:
            _known(rec, k, r, RECORD)
    for r in rec.get("servers") or []:
        _known(rec, "servers", r, ("kind", "host", "note"))
    _known(rec, "links", rec.get("links") or {}, LINKS)
    _known(rec, "security", rec.get("security") or {}, [s for s, _ in SECURITY])
    out = {}
    for k in KEYS:
        v = rec.get(k)
        if v is None or v is False or v == "" or v == [] or v == {}:
            continue
        if k in ("a", "mx"):
            v = [_Flow((f, r.get(f, "")) for f in RECORD) for r in v]
        elif k == "servers":
            v = [_Flow((f, r[f]) for f in ("kind", "host", "note") if r.get(f)) for r in v]
        elif k in ("links", "security"):
            order = LINKS if k == "links" else [s for s, _ in SECURITY]
            v = {f: v[f] for f in order if v.get(f)}
            if not v:
                continue
        out[k] = v
    text = yaml.dump(out, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=4096, default_flow_style=False)
    return "# eMailCompare provider record; fields are described in data/README.md\n" + text


class _Loader(yaml.SafeLoader):
    """Reads YAML the way Hugo (a YAML 1.2 parser) does: no dates, no yes/no/on/off booleans, no sexagesimal
    numbers; those stay text."""


_Loader.yaml_implicit_resolvers = {}
for _tag, _regexp, _first in (
        ("null", r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""]),
        ("bool", r"^(?:true|True|TRUE|false|False|FALSE)$", list("tTfF")),
        ("int", r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$", list("-+0123456789")),
        ("float", r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
         list("-+0123456789."))):
    _Loader.add_implicit_resolver("tag:yaml.org,2002:" + _tag, re.compile(_regexp), _first)


def loads(text):
    return yaml.load(text, Loader=_Loader)  # noqa: S506  (a SafeLoader subclass)


def load(domain):
    with open(path(domain), encoding="utf-8") as f:
        return loads(f.read())


def load_all(directory=None):
    directory = directory or DIR
    recs = []
    for p in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        with open(p, encoding="utf-8") as f:
            rec = loads(f.read())
        if not isinstance(rec, dict):
            raise ValueError(f"{p}: not a YAML mapping of fields")
        rec["_file"] = os.path.basename(p)
        recs.append(rec)
    recs.sort(key=lambda r: r["order"])
    return recs


def _clean(v):
    """The record as dumps() writes it: empty values and dead: false left out."""
    if isinstance(v, dict):
        out = {k: _clean(x) for k, x in v.items()}  # clean first: a mapping can become empty
        return {k: x for k, x in out.items() if not (x is None or x is False or x == "" or x == [] or x == {})}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def save(rec, directory=None):
    """Write a record; refuses to write text that does not read back to the same record."""
    directory = directory or DIR
    rec = {k: v for k, v in rec.items() if not k.startswith("_")}
    text = dumps(rec)
    want = _clean(rec)
    for k in ("a", "mx"):
        if k in want:
            want[k] = [{f: r.get(f, "") for f in RECORD} for r in want[k]]
    if loads(text) != want:
        raise ValueError(f"{rec.get('domain')}: the YAML text would not read back to the same record")
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, file_name(rec["domain"]))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".yaml")  # atomic: never half a file
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        try:  # keep the old file's mode; a new file gets what open() would give (mkstemp alone: rw-------)
            mode = os.stat(target).st_mode & 0o777
        except OSError:
            mode = 0o666 & ~_UMASK
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------------------------------- CSV row <-> record
def _lines(cell):
    return [l.strip() for l in cell.split("\n") if l.strip()]


def _record_line(line):
    """"host | 🇨🇭 | Switzerland | AS62371 | Org" -> record; parts the collector could not fill stay empty
    ("host | ?" when the IP lookup failed)."""
    parts = [p.strip() for p in line.split("|")]
    parts += [""] * (5 - len(parts))
    if parts[1] == "?":
        parts[1] = ""
    return {"host": parts[0], "flag": parts[1], "country": parts[2], "asn": parts[3], "org": " | ".join(parts[4:]).strip(" |")}


def clean_cell(cell):
    """What a cell means: outer whitespace dropped, Windows line breaks as \n (Hugo's CSV reader did the same)."""
    return cell.replace("\r\n", "\n").replace("\r", "\n").strip()


def from_row(cells, status, dead, order):
    c = [clean_cell(x) for x in cells]
    if len(c) != len(HEADER):
        raise ValueError(f"row has {len(c)} cells")
    alliance = {v: k for k, v in ALLIANCE.items()}
    if c[8] and c[8] not in alliance:
        raise ValueError(f"{c[1]}: unknown alliance {c[8]!r}")
    security = {}
    if c[24]:
        names = dict((n, k) for k, n in SECURITY)
        for part in c[24].split(";"):
            name, value = (s.strip() for s in part.split(":", 1))
            security[names[name]] = value
    servers = []
    for line in _lines(c[25]):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (2, 3):
            raise ValueError(f"{c[1]}: server line {line!r}")
        servers.append(dict(zip(("kind", "host", "note"), parts)))
    return {
        "order": order, "domain": c[1], "status": status, "dead": dead, "flag": c[0],
        "domains": _lines(c[2]), "a": [_record_line(l) for l in _lines(c[3])],
        "mx": [_record_line(l) for l in _lines(c[4])], "created": c[5], "expires": c[6], "owner": c[7],
        "alliance": alliance.get(c[8]), "jurisdiction": _lines(c[9]), "description": c[10],
        "tos_summary": c[11], "privacy_summary": c[12], "email": c[13], "phone": c[14], "personal": c[16],
        "price": [p.strip() for p in c[15].split(";") if p.strip()], "note": c[17],
        "links": {"tos": c[18], "privacy": c[19], "code": c[20], "imprint": c[23]},
        "social": _lines(c[21]), "onion": c[22], "security": security, "servers": servers,
        "online_since": c[26], "last_checked": c[27],
    }


def to_row(rec):
    g = rec.get
    links = g("links") or {}
    sec = g("security") or {}
    rec_line = lambda r: " | ".join(r.get(f, "") for f in RECORD)  # noqa: E731
    return [
        g("flag", ""), g("domain", ""), "\n".join(g("domains") or []),
        "\n".join(rec_line(r) for r in g("a") or []), "\n".join(rec_line(r) for r in g("mx") or []),
        g("created", ""), g("expires", ""), g("owner", ""), ALLIANCE.get(g("alliance"), ""),
        "\n".join(g("jurisdiction") or []), g("description", ""), g("tos_summary", ""), g("privacy_summary", ""),
        g("email", ""), g("phone", ""), "; ".join(g("price") or []), g("personal", ""), g("note", ""),
        links.get("tos", ""), links.get("privacy", ""), links.get("code", ""), "\n".join(g("social") or []),
        g("onion", ""), links.get("imprint", ""),
        "; ".join(f"{name}: {sec[k]}" for k, name in SECURITY if k in sec),
        "\n".join(" | ".join(s[f] for f in ("kind", "host", "note") if s.get(f)) for s in g("servers") or []),
        g("online_since", ""), g("last_checked", ""),
    ]

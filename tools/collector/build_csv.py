#!/usr/bin/env python3
"""Turn the collected DNS/RDAP/web data into a CSV in the same format as emails.csv.

usage: build_csv.py DOMAINS_TSV DNS_DIR WEB_DIR EMAILS_CSV OUT_CSV REVIEW_MD [DEEP_DIR]

EMAILS_CSV is only read for its header row. DEEP_DIR holds collect_deep.py results (optional).
Downloaded page text is treated as data only.
"""
import concurrent.futures as cf
import csv
import datetime
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import emcdata  # noqa: E402

DOMAINS_TSV, DNS_DIR, WEB_DIR, EMAILS_CSV, OUT_CSV, REVIEW_MD = sys.argv[1:7]
DEEP_DIR = sys.argv[7] if len(sys.argv) > 7 else ""
# hand-checked corrections, keyed by domain and column ("Note" is appended, other columns are replaced)
OVERRIDES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overrides.json")
TODAY = datetime.date.today().isoformat()

# --------------------------------------------------------------------------- helpers
def flag(cc):
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)


# ccTLDs that normally mean "this service is from that country" (generic-use ones like .io/.me/.cc are left out)
NATIONAL = {t: t.upper() for t in
            "de it nl ch dk gr pt cz hu uy se hr ca ar au il in my fr es be at pl no fi ee lv lt sk si ro bg rs ua "
            "ru jp kr br mx cl za nz ie lu".split()}
NATIONAL["uk"] = "GB"
FIVE, NINE, FOURTEEN = {"US", "GB", "CA", "AU", "NZ"}, {"DK", "FR", "NL", "NO"}, {"DE", "BE", "IT", "ES", "SE"}
PRIVACY_ORG = re.compile(r"privacy|redact|withheld|proxy|protect|guard|gdpr|not disclosed|whoisguard|domains by proxy|"
                         r"contact privacy|identity|anonym|statutory|mask|hidden|data protected|private|"
                         r"1337 services|njalla|personal data|n/a", re.I)


def alliance(ccs):
    levels = [5 if c in FIVE else 9 if c in NINE else 14 if c in FOURTEEN else None for c in ccs]
    levels = [lv for lv in levels if lv]
    if not levels:
        return ""
    return {5: "Five Eyes (👀 5)", 9: "Nine Eyes (👀 9)", 14: "Fourteen Eyes (👀 14)"}[min(levels)]


def read(path, limit=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read() if limit is None else f.read(limit)
    except FileNotFoundError:
        return ""


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]


NEG = re.compile(r"\b(?:not|never|no|don.?t|doesn.?t|won.?t|can.?t|cannot|isn.?t|aren.?t|unable|neither|nor|without|"
                 r"nicht|kein\w*|niemals|ohne|non|mai|"
                 r"pas|jamais|nunca|nie|niet|geen|inte|aldrig|sem|sin|senza|sans)\b", re.I)
EXCEPT = re.compile(r"unless|except|only (?:if|when|upon)|required by (?:law|a court)|court order|subpoena|soweit|"
                    r"sofern|es sei denn|au(?:ss|ß)er|salvo|tranne|sauf|excepto|a meno che", re.I)


def polarity(text, pattern, skip=None, min_words=4):
    """None if never mentioned, 'pos' if mentioned affirmatively at least once, 'neg' if only negated.

    By default headings like "IP addresses" carry no polarity, so only real sentences (4+ words) count.
    """
    hits = [s for s in sentences(text) if len(s.split()) >= min_words and re.search(pattern, s, re.I)
            and not (skip and re.search(skip, s, re.I))]
    if not hits:
        return None
    return "pos" if any(EXCEPT.search(s) or not NEG.search(s) for s in hits) else "neg"


UNIT = (r"(days?|weeks?|months?|years?|tage?n?|wochen|monate?n?|jahre?n?|giorni|settimane|mesi|anni|jours|semaines|"
        r"mois|ans|d[ií]as|semanas|meses|a[ñn]os|dni|tygodni|miesi[ęe]cy|lat|dagen|weken|maanden|jaar|dage|"
        r"m[åa]neder|dagar|m[åa]nader)")


def to_period(num, unit):
    n, u = int(num), unit.lower()
    if re.match(r"(?:day|tag|giorn|jour|d[ií]a|dni|dag)", u):
        return f"{n} days" if n < 60 else f"{round(n / 30)} mos."
    if re.match(r"(?:week|woche|settiman|semaine|semana|tygod|week)", u):
        return f"{n} weeks"
    if re.match(r"(?:month|monat|mes|mois|miesi|maand|m[åa]ned|m[åa]nad)", u):
        return f"{n} mos."
    return f"{n * 12} mos."


INACTIVE = [
    re.compile(r"(?:inactiv|inaktiv|unused|not (?:been )?used|not logged in|no login|dormant|nicht (?:mehr )?(?:genutzt|"
               r"benutzt|verwendet)|inattiv|inutilis|inactif|sin uso|nieaktywn|nepou[žz][íi]v)[^.\n]{0,160}?(\d{1,3})\s*"
               + UNIT, re.I),
    re.compile(r"(\d{1,3})\s*" + UNIT + r"[^.\n]{0,60}?(?:of )?(?:inactiv|inaktiv|no (?:use|login|activity)|without (?:use|"
               r"login|logging in)|ohne (?:nutzung|login|anmeldung)|nicht (?:genutzt|benutzt)|di inattivit|"
               r"d.inactivit|de inactividad|bez aktywno)", re.I),
]


def inactive_period(text):
    for rx in INACTIVE:
        m = rx.search(text)
        if m:
            return to_period(m.group(1), m.group(2))
    return None


RETENTION = re.compile(r"(?:retain|retention|stored for|kept for|keep[^.\n]{0,40}for|deleted after|delete[^.\n]{0,40}after|"
                       r"gel[öo]scht nach|l[öo]sch[^.\n]{0,40}nach|aufbewahr|speicher[^.\n]{0,40}(?:f[üu]r|bis zu)|"
                       r"conserv|cancell[^.\n]{0,30}dopo|supprim[^.\n]{0,30}apr[èe]s|elimin[^.\n]{0,30}despu[ée]s|"
                       r"usuwa[^.\n]{0,30}po)[^.\n]{0,80}?(\d{1,3})\s*" + UNIT, re.I)

TOS_ILLEGAL_SUBS = [
    ("CSAM", r"child (?:sexual|abuse|porn)|csam|sexual(?:ly)? (?:abuse|exploitation) of (?:children|minors)|"
             r"kinderporno|pedopornogra|pornographie (?:infantile|enfantine)|pornograf[ií]a infantil|"
             r"pornografia minorile|p[ae]e?dophil|pedofil"),
    ("spam", r"\bspam|unsolicited|bulk (?:e-?mail|messag)|mass (?:e-?mail|mailing)|massen-?(?:e-?)?mail|"
             r"unerw[üu]nschte werbung|posta indesiderata|non sollicit"),
    ("hacking", r"\bhack|malware|\bvirus|\bviren|phishing|botnet|ddos|denial[- ]of[- ]service|exploit|"
                r"unauthori[sz]ed access|trojan|ransomware|port ?scan"),
]
TOS_ILLEGAL = (r"illegal|unlawful|rechtswidrig|gesetzwidrig|ill[ée]gal|illicite|ilegal|il[íi]cit|nielegal|nez[áa]kon|"
               r"illegaal|olaglig|jogellenes|nezakonit|παράνομ|unzul[äa]ssig|criminal|strafbar")
TOS_OTHER = [
    ("IP violation", r"copyright|intellectual property|urheberrecht|diritt[oi] d.autore|droits? d.auteur|"
                     r"propiedad intelectual|propriedade intelectual|trademark|auteursrecht|prawa? autorski|"
                     r"autorsk[áa] pr[áa]va|upphovsr[äa]tt|ophavsret|szerz[őo]i jog"),
    ("harassment", r"harass|threat|insult|defamat|hate speech|racis|beleidig|bedroh|diffam|harc[èe]lement|acoso|"
                   r"molest|mobbing|stalk|n[ęe]kani"),
    ("fraud", r"fraud|scam|betrug|frode|truffa|fraude|escroquer|estafa|oszust|podvod|bedrog|csal[áa]s"),
    ("commercial use", r"commercial (?:use|purpose|activit)|business purposes|gewerbliche? nutzung|"
                       r"kommerzielle? nutzung|uso commerciale|usage commercial|uso comercial|fins? commerciaux|"
                       r"u[żz]ytek komercyjny"),
    ("multiple accounts", r"multiple accounts|more than one account|mehrere (?:konten|accounts|postf[äa]cher)|"
                          r"pi[uù] account|plusieurs comptes|varias cuentas|wiele kont"),
]


def summarize_tos(text):
    if len(text) < 300:
        return ""
    low = text.lower()
    subs = [name for name, rx in TOS_ILLEGAL_SUBS if re.search(rx, low)]
    parts = []
    if re.search(TOS_ILLEGAL, low) or subs:
        parts.append("Illegal actions" + (f" ({', '.join(subs)})" if subs else ""))
    parts += [name for name, rx in TOS_OTHER if re.search(rx, low)]
    lines = [", ".join(parts)] if parts else []
    period = inactive_period(text)
    if period:
        lines.append(f"Delete inactive accounts ({period})")
    return "\n".join(lines)


def summarize_privacy(text):
    if len(text) < 300:
        return ""
    items = []
    ip = polarity(text, r"\bip[- ]?(?:address|adresse|adres|adresa|c[íi]m)|indirizz[oi] ip|adresse ip|"
                        r"direcci[oó]n ip|endere[çc]o ip")
    if ip:
        items.append("collect IP addresses" if ip == "pos" else "no IP logging (claimed)")
    m = RETENTION.search(text)
    if m:
        items.append(f"data/logs kept {to_period(m.group(1), m.group(2))}")
    gdpr_objection = (r"widerspr|right to object|object to (?:the )?processing|droit d.opposition|"
                      r"diritto di opposizione|derecho (?:de|a) oponer|direktwerbung|direct marketing|profiling")
    for label, rx, neg_label, skip in [
        ("cookies", r"cookie", None, None),
        ("analytics/tracking", r"google analytics|\banalytics\b|matomo|piwik|plausible|(?:user|web|third[- ]party|"
                               r"cross[- ]site|ad|advertising|behaviou?ral) tracking|tracking (?:cookies?|pixels?|"
                               r"scripts?|technolog\w*|tools?)|\btrackers?\b", "no tracking (claimed)", None),
        ("advertising", r"advertis|werbung|pubblicit|publicit[ée]|publicidad|publicidade|reklam", "no ads (claimed)",
         gdpr_objection),
        ("third parties", r"third[- ]part|\bdritte[nr]?\b|terze parti|\bterzi\b|\btiers\b|terceros|terceiros|"
                          r"osob(?:y|om) trzeci|derden|tredje ?part", "no third-party sharing (claimed)", None),
    ]:
        p = polarity(text, rx, skip)
        if p == "pos":
            items.append(label)
        elif p == "neg" and neg_label:
            items.append(neg_label)
    # skip the GDPR "you may complain to a supervisory authority" boilerplate
    complaint = (r"beschwerde|complaint|aufsichtsbeh|supervisory authorit|data protection authorit|datenschutzbeh|"
                 r"reclamo|r[ée]clamation|garante|klage")
    if any(re.search(r"law enforcement|authorit(?:y|ies)|court order|subpoena|warrant|legal (?:request|process)|"
                     r"beh[öo]rde|strafverfolgung|gerichtlich|autorit[àa] giudiziari|autorit[ée]s|autoridad|"
                     r"\bpoli(?:ce|zei|zia|cja)\b|organy [śs]cigania|politie|myndighet", s, re.I)
           and not re.search(complaint, s, re.I) for s in sentences(text)):
        items.append("share upon authorities' request")
    if re.search(r"encrypt|verschl[üu]ssel|crittograf|cifrad|chiffr|szyfr|versleutel|kryptera|titkos[íi]t", text, re.I):
        items.append("encryption")
    if re.search(r"no logs|don.?t (?:keep |store |collect )?logs?|do not (?:keep |store |collect )?(?:any )?logs?|"
                 r"keine logs|keine (?:ip-?adressen|protokolle)|nessun log|aucun log", text, re.I):
        items.append("no-logs claim")
    if re.search(r"\bgdpr\b|dsgvo|\brgpd\b|general data protection regulation", text, re.I):
        items.append("GDPR")
    sell = polarity(text, r"\bsell(?:s|ing)? (?:your |any )?(?:personal )?(?:data|information)|verkauf\w* (?:ihre|deine|"
                          r"personenbezogene|ihrer) daten|vend\w* (?:i |tes |tus |vos )?(?:dati|donn[ée]es|datos)")
    if sell == "pos":
        items.append("may sell data")
    elif sell == "neg":
        items.append("does not sell data (claimed)")
    period = inactive_period(text)
    if period:
        items.append(f"delete inactive accounts ({period})")
    if not items:
        return ""
    out = ", ".join(items)
    return out[0].upper() + out[1:]


FREE = re.compile(r"free (?:plan|account|tier|version|e-?mail|mailbox|forever|of charge|service|basic)|\bfor free\b|"
                  r"100% free|completely free|totally free|always free|\bis free\b|\bare free\b|"
                  r"(?<![\w-])(?:kostenlos\w*|gratis|gratuit[eo]?|gratuita|darmow\w*|zdarma|ingyenes|δωρεάν|"
                  r"besplatn\w*|ilmai\w*)(?![\w-])", re.I)
NOT_PRICE_FREE = re.compile(r"free trial|try [\w ]{0,20}free|free for \d+[- ]days?|feel free|free software|"
                            r"free and open|free/libre|free speech|freedom|probemonat|prova gratuita|essai gratuit|"
                            r"prueba gratuita|kostenlos testen", re.I)
PRICE = re.compile(r"((?:€|\$|£|EUR|USD|CHF|GBP|PLN|zł|CZK|Kč|SEK|DKK|NOK|HUF|Ft)\s?\d{1,4}(?:[.,]\d{1,2})?|"
                   r"(?<![\d'’.,])\d{1,4}(?:[.,]\d{1,2})?\s?(?:€|\$|£|EUR|USD|CHF|GBP|PLN|zł|CZK|Kč|SEK|DKK|NOK|HUF|Ft))"
                   r"\s*(?:/|per|a|pro|al|par|por|za|/\s?)\s*(month|mo|year|yr|monat|jahr|mese|anno|mois|an|mes|"
                   r"a[ñn]o|m[êe]s|ano|miesi[ąa]c|rok|m[ěe]s[íi]c|m[åa]ned|[åa]r|m[åa]nad)\b", re.I)


def summarize_price(home, pricing, signup, pricing_url):
    out = []
    free_hits = [s for s in sentences("\n".join((home, pricing, signup)))
                 if FREE.search(s) and not NOT_PRICE_FREE.search(s)]
    if free_hits:
        out.append("free")
    seen = []
    for m in PRICE.finditer(pricing + "\n" + home):
        amount = re.sub(r"\s+", "", m.group(1))
        period = "month" if re.match(r"mo|monat|mese|mois|mes|m[êe]s|miesi|m[ěe]s|m[åa]ne|m[åa]na", m.group(2), re.I) \
            else "year"
        try:
            is_zero = float(re.sub(r"[^\d.,]", "", amount).replace(",", ".")) == 0
        except ValueError:
            is_zero = False
        if is_zero:  # "$0/month" is the free plan
            if "free" not in out:
                out.insert(0, "free")
            continue
        p = f"{amount}/{period}"
        if p not in seen:
            seen.append(p)
    out += seen[:3]
    if not seen and not re.search(r"donat|spende", pricing_url or "", re.I):
        # one-off crypto prices such as "PRICE / RATE LIMIT 0.005 XMR"; donation totals are not prices
        text = pricing + "\n" + home
        for m in re.finditer(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s?(XMR|BTC|LTC)\b", text):
            before = text[max(0, m.start() - 60):m.start()]
            if not re.search(r"price|cost|per\b|buy|purchase|\bpay|fee|plan|for sale|rate|preis|kosten|prezzo|prix|"
                             r"precio", before, re.I):
                continue
            p = f"{m.group(1)} {m.group(2).upper()}"
            if p not in out:
                out.append(p)
    if not out and re.search(r"donat|spende|spenden|donazion|don\b|doa[çc][ãa]o", (pricing_url or "") + pricing[:3000], re.I):
        out.append("donations")
    return "; ".join(out)


LEGAL_FORM = (r"(?:GmbH(?: & Co\.? KG)?|AG|e\.\s?V\.|UG(?: \(haftungsbeschr[äa]nkt\))?|KG|Ltd\.?|Limited|LLC|L\.L\.C\.|"
              r"Inc\.?|Corp\.?|Corporation|S\.r\.l\.|Srl|SRL|S\.p\.A\.|SpA|S\.A\.S\.?|SAS|SARL|S\.A\.|B\.V\.|N\.V\.|ApS|"
              r"A/S|AB|Oy|OÜ|s\.r\.o\.|[Ss]p\. z o\.\s?o\.|Kft\.?|Zrt\.?|d\.o\.o\.|S\.L\.|Lda\.?|Pty\.? Ltd\.?|ehf\.?|"
              r"PLT|Sdn\.? Bhd\.?|LLP|PLC|plc|Stiftung|Foundation|Association|Cooperative|SCRL|SCE)")
COMPANY = re.compile(r"((?:[A-Z0-9ÄÖÜÉÈÀÇŁŚŻ][\w&'’.\-]*\s+){1,5}" + LEGAL_FORM + r")(?![\w])")
BAD_COMPANY = re.compile(r"\bsite by\b|designed by|made (?:by|with)|terms|privacy|policy|copyright|rights|cookie|service|email|e-mail|mail account|"
                         r"\bwe\b|\bour\b|\bthe\b|\bthis\b", re.I)


COPYRIGHT_YEAR_FIRST = re.compile(r"(?:©|\(c\)|copyright)\s*\d{4}(?:\s*[-–—]\s*\d{4})?\s*,?\s*(?:by\s+)?"
                                  r"([A-Za-z0-9][\w&'’.\- ]{1,50}?)(?=\s*(?:[.|•·,©]|all rights|alle rechte|"
                                  r"tutti i diritti|tous droits|todos los derechos|\d{4}|$))", re.I | re.M)
COPYRIGHT_NAME_FIRST = re.compile(r"(?:©|\(c\)|copyright)\s*(?:by\s+)?([A-Za-z][\w&'’.\- ]{1,50}?)\s*,?\s*\d{4}",
                                  re.I | re.M)


# hosting providers, processors, registrars, banks and platforms that policies mention but that don't run the service
THIRD_PARTY_ORG = re.compile(
    r"hetzner|\bovh|netcup|contabo|digitalocean|linode|akamai|strato|ionos|1&1|hostinger|vultr|scaleway|leaseweb|"
    r"\bm247\b|cloudflare|amazon|google|microsoft|apple inc|meta platforms|facebook|paypal|stripe|mollie|coinbase|"
    r"bitpay|free software foundation|electronic frontier|tor project|mozilla|let.?s encrypt|internet security research|"
    r"github|gitlab|wikimedia|postfinance|\bbank\b|sparkasse|\bgls\b|iban|kontoverbindung|tucows|namecheap|godaddy|"
    r"gandi|porkbun|epik|inwx|united-domains|key-systems|cronon|denic|eurid|nominet|verisign|icann|automattic|"
    r"wordpress|squarespace|wix\.com|shopify|hubspot|mailchimp|sendgrid|twilio|zendesk|intercom|matomo|plausible|"
    r"hcaptcha|recaptcha|youtube|twitter|linkedin|instagram", re.I)
NOISE_LEAD = {"postadres", "adres", "address", "adresse", "anschrift", "betreiber", "operator", "anbieter",
              "herausgeber", "inhaber", "firma", "company", "provider", "by", "von", "der", "die", "das", "the", "and",
              "und", "of", "für", "for", "support", "affiliates", "favorites", "copyright", "owned", "operated",
              "powered", "hosted", "sitz", "registered", "office", "c/o"}
GENERIC_WORDS = {"mail", "email", "e", "inbox", "webmail", "online", "service", "services", "team", "project", "net",
                 "the", "secure", "private"}
FORM_COUNTRY = [(r"\bOÜ$", "EE"), (r"sp\. z o\.\s?o\.?$", "PL"), (r"S\.p\.A\.?$|S\.r\.l\.?$|\bSrl$|\bSRL$|\bSpA$", "IT"),
                (r"\bApS$|\bA/S$", "DK"), (r"\bOy$", "FI"), (r"\behf\.?$", "IS"), (r"\bKft\.?$|\bZrt\.?$", "HU"),
                (r"\bS\.L\.?$", "ES"), (r"\bLda\.?$", "PT"), (r"\bPLT$|Sdn\.? Bhd\.?$", "MY"),
                (r"Pty\.? Ltd\.?$", "AU"), (r"\bB\.V\.?$", "NL"), (r"\be\.\s?V\.?$|\bUG(?: \(haftungsbeschr[äa]nkt\))?$", "DE")]


def clean_name(name):
    name = " ".join(name.split()).strip(" .,-–|")
    name = re.sub(r"\b(?:all rights reserved|alle rechte vorbehalten|tutti i diritti riservati)\b.*$", "", name,
                  flags=re.I).strip(" .,-")
    name = re.split(r"(?<=[a-z0-9])\.\s+(?=[A-Z])", name)[-1]  # "Foo bar. Baz GmbH" -> "Baz GmbH"
    words = name.split()
    while words and (re.fullmatch(r"[\d\W]+|[A-Za-z]\.", words[0]) or words[0].lower().strip(":,") in NOISE_LEAD):
        words = words[1:]
    return " ".join(words)


def brand_only(owner, domain):
    """True for names like 'nix' (nix.to) or 'aikQ Mail' (aikq.de) that just repeat the brand."""
    if re.search(LEGAL_FORM + r"\.?$", owner):
        return False
    dn = re.sub(r"[^a-z0-9]", "", domain.lower())
    words = [w for w in re.split(r"[^a-z0-9]+", owner.lower()) if w]
    return bool(words) and all(w in dn or w in GENERIC_WORDS for w in words)


def pick_owner(home, imprint, tos, privacy, asn_orgs):
    """Most plausible operator name: copyright holder / legal-form names, ranked by how often they appear."""
    cands = {}

    def add(name, bonus):
        name = clean_name(name)
        if not 3 <= len(name) <= 60 or BAD_COMPANY.search(name) or THIRD_PARTY_ORG.search(name) \
                or re.fullmatch(r"[\d\W]+", name) or re.fullmatch(LEGAL_FORM + r"\.?", name):
            return
        cands.setdefault(name.lower(), [name, 0])[1] += bonus

    footer = home[-5000:]
    for rx in (COPYRIGHT_YEAR_FIRST, COPYRIGHT_NAME_FIRST):
        for m in rx.finditer(footer):
            add(m.group(1), 3)
    for text, bonus in ((imprint[:8000], 2), (footer, 1), (tos[:6000], 1), (privacy[:6000], 1)):
        for m in COMPANY.finditer(text):
            add(m.group(1), bonus)
    corpus = "\n".join((home, imprint, tos, privacy)).lower()
    best, best_score = "", 0
    for key, (name, bonus) in cands.items():
        score = bonus + min(corpus.count(key), 10)
        first = re.split(r"\W+", key)[0]
        if len(first) >= 4 and any(first in (o or "").lower() for o in asn_orgs):
            score += 3
        if score > best_score:
            best, best_score = name, score
    return best


COUNTRY_WORDS = {
    "DE": r"germany|deutschland|allemagne|germania|alemania|niemcy", "AT": r"austria|österreich|autriche",
    "CH": r"switzerland|schweiz|suisse|svizzera", "NL": r"netherlands|nederland|niederlande|pays-bas|paesi bassi",
    "FR": r"france|frankreich|francia", "IT": r"italy|italia|italien|italie",
    "ES": r"spain|españa|espana|spanien|espagne|catalunya|catalonia", "PT": r"portugal",
    "BE": r"belgium|belgique|belgië|belgien", "SE": r"sweden|sverige|schweden|suède", "NO": r"norway|norge|norwegen",
    "DK": r"denmark|danmark|dänemark", "FI": r"finland|suomi|finnland", "IS": r"iceland|ísland",
    "PL": r"poland|polska|polen", "CZ": r"czech republic|czechia|česká republika|tschechien",
    "HU": r"hungary|magyarország|ungarn", "GR": r"greece|ελλάδα|griechenland", "HR": r"croatia|hrvatska|kroatien",
    "RS": r"serbia|srbija|serbien", "RO": r"romania|românia|rumänien", "EE": r"estonia|eesti|estland",
    "GB": r"united kingdom|england|scotland|wales|great britain|großbritannien", "IE": r"ireland|irland",
    "US": r"united states|usa|u\.s\.a\.", "CA": r"canada|kanada", "AU": r"australia|australien",
    "BR": r"brazil|brasil|brasilien", "AR": r"argentina|argentinien", "UY": r"uruguay",
    "MX": r"mexico|méxico|mexiko", "CO": r"colombia|kolumbien", "IN": r"india|indien", "MY": r"malaysia",
    "SG": r"singapore|singapur", "IL": r"israel", "PA": r"panama|panamá", "SC": r"seychelles",
    "CY": r"cyprus|zypern", "LU": r"luxembourg|luxemburg", "LI": r"liechtenstein", "BG": r"bulgaria|bulgarien",
    "UA": r"ukraine|україна", "RU": r"russia|russland|россия",
}


def imprint_country(imprint):
    counts = {cc: len(re.findall(r"\b(?:" + rx + r")\b", imprint[:8000], re.I)) for cc, rx in COUNTRY_WORDS.items()}
    cc, n = max(counts.items(), key=lambda kv: kv[1])
    return cc if n else ""


CLAIMS = [
    ("no phone number required", r"no (?:phone|mobile)(?: number)?(?: required| needed| verification)|"
                                 r"without (?:a |any )?(?:phone|mobile)(?: number)?|no phone number|"
                                 r"keine (?:telefon|handy)nummer|ohne (?:telefon|handy)nummer|senza (?:numero di )?telefono|"
                                 r"sans (?:num[ée]ro de )?t[ée]l[ée]phone|sin (?:n[úu]mero de )?tel[ée]fono"),
    ("no personal data required", r"no personal (?:data|information|details)(?: (?:is |are )?(?:required|needed))?|"
                                  r"without (?:any |providing )?personal (?:data|information)|"
                                  r"keine pers[öo]nlichen (?:daten|angaben)|ohne pers[öo]nliche (?:daten|angaben)|"
                                  r"nessun dato personale|aucune donn[ée]e personnelle|"
                                  r"anonymous (?:sign ?-?up|registration|accounts?)|anonyme (?:registrierung|anmeldung)"),
    ("invite-only", r"invite[- ]only|invitation code|invite code|einladungs?code|nur (?:auf|mit) einladung|"
                    r"codice (?:di )?invito|by invitation only"),
    ("accepts cryptocurrency", r"monero|\bxmr\b|bitcoin|\bbtc\b|cryptocurrenc|kryptow[äa]hrung"),
    ("PGP/E2E encryption", r"openpgp|\bpgp\b|\bgpg\b|end-to-end|\be2ee\b|ende-zu-ende"),
]
CHALLENGE = re.compile(r"just a moment|attention required|ddos-guard|checking your browser|access denied|"
                       r"please enable js|enable javascript and cookies|captcha|are you a robot|403 forbidden|"
                       r"making sure you.?re not a bot|permanent redirect", re.I)
MAINTENANCE = re.compile(r"maintenance|outage|status update|coming soon|coming q\d|under construction|wartung", re.I)
# unrelated SEO spam injected into a hacked or abandoned site
SPAM = re.compile(r"\bcasinos?\b|gamstop|\bviagra\b|\bcialis\b|\bslot (?:online|gacor)|\bjudi\b|\btogel\b|"
                  r"payday loans?|replica watches", re.I)
MAIL_WORDS = re.compile(r"e-?mail|\bmail|correo|posta|courriel|почт|e-?post|επιστολ|דואר|webmail|imap|smtp", re.I)


def dig_mx(name):
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=2", name, "MX"], capture_output=True, text=True,
                             timeout=30).stdout
    except subprocess.TimeoutExpired:
        return []
    hosts = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            hosts.append(parts[1].rstrip(".").lower())
    return hosts


def dig_a(name):
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=2", name, "A"], capture_output=True, text=True,
                             timeout=30).stdout
    except subprocess.TimeoutExpired:
        return []
    return [l for l in out.split() if re.fullmatch(r"[\d.]+", l)]


def base2(host):
    return ".".join(host.split(".")[-2:])


# mail hosted by a third party (white label or forwarding): the domain's owner does not run the servers
MAIL_HOSTS = [("Google Workspace", r"google\.com$|googlemail\.com$"), ("Microsoft 365", r"outlook\.com$"),
              ("Fastmail", r"messagingengine\.com$"), ("Proton Mail", r"protonmail\.ch$"), ("Migadu", r"migadu\.com$"),
              ("mailbox.org", r"mailbox\.org$"), ("Zoho Mail", r"zoho\.(?:com|eu|in)$"), ("MXroute", r"mxrouting\.net$"),
              ("Purelymail", r"purelymail\.com$"), ("Cloudflare Email Routing", r"cloudflare\.net$"),
              ("ImprovMX", r"improvmx\.com$"), ("Forward Email", r"forwardemail\.net$"), ("Yandex", r"yandex\.(?:net|ru)$"),
              ("Mail.ru", r"mail\.ru$"), ("Titan", r"titan\.email$"), ("Namecheap Private Email", r"privateemail\.com$"),
              ("OVH", r"ovh\.net$"), ("Gandi", r"gandi\.net$"), ("Hostinger", r"hostinger\.com$"),
              ("Mailgun", r"mailgun\.org$"), ("Posteo", r"posteo\.de$"), ("Tutanota", r"tutanota\.de$"),
              ("Infomaniak", r"infomaniak\.ch$"), ("IONOS", r"ionos\.\w+$|1and1\.\w+$|kundenserver\.de$"),
              ("Hostpoint", r"hostpoint\.ch$"), ("Strato", r"rzone\.de$"), ("Runbox", r"runbox\.com$"),
              ("SiteGround", r"mailspamprotection\.com$"), ("Barracuda", r"barracudanetworks\.com$"),
              ("Mimecast", r"mimecast\.com$"), ("Proofpoint", r"pphosted\.com$"), ("USA.net", r"usa\.net$")]
# hostname labels in front of the provider domain in TLS certificates (mail.example.org -> example.org)
HOST_LABELS = re.compile(r"^(?:www|mail|webmail|email|imap|smtp|pop3?|mx\d*|autoconfig|autodiscover|mta-sts|"
                         r"smtp-?(?:in|out)\d*|mail\d+|correo|posta|post|mailserver|ms\d*)\.", re.I)


def registrable_of(name):
    """Rough registrable domain: strip mail host labels, keep three labels for co.uk-style suffixes."""
    name = name.lower().strip(".").lstrip("*.")
    while HOST_LABELS.match(name) and name.count(".") > 1:
        name = HOST_LABELS.sub("", name, count=1)
    labels = name.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def short_date(ts):
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if ts and len(ts) >= 8 else ts or ""


BIG_MX = re.compile(r"google\.com$|googlemail\.com$|outlook\.com$|cloudflare\.net$|messagingengine\.com$", re.I)
PLACEHOLDER = re.compile(r"^(?:example|your|my|domain|company|ourcompany|test|sample|foo|bar|acme|someone|user|"
                         r"username|name|email|mail)[\w-]*\.(?:com|org|net|tld|de|it)$|\.(?:tld|example|invalid|test|"
                         r"local|onion)$", re.I)


def review_details(w, x):
    """Extra lines for review.md: redirect chains, crawled pages and the deep checks."""
    out = []
    for t in w.get("tried", []):
        hops = " → ".join(f"{h['url']} [{h.get('status') or h.get('error', '?')}{'/' + h['kind'] if h.get('kind') not in (None, 'http') else ''}]"
                          for h in t.get("chain") or [])
        out.append(("tried", f"{t.get('state')} ({t.get('ua')} UA): {hops or t.get('error')}"))
    for cat, p in (w.get("pages") or {}).items():
        if p:
            out.append((f"page {cat}", f"{p.get('final_url') or p.get('url')} [{p.get('from') or p.get('error')}]"))
    if w.get("sitemap") is not None:
        out.append(("sitemap categories", ", ".join(w["sitemap"]) or "none"))
    for key in ("ns", "spf", "dmarc", "mta_sts", "tls_rpt", "dane", "dnssec"):
        if x.get(key) not in (None, "", []):
            out.append((key, x[key] if not isinstance(x[key], list) else ", ".join(x[key])))
    for name, recs in (x.get("srv") or {}).items():
        out.append((name, "; ".join(recs)))
    if x.get("subdomains"):
        out.append(("subdomains", ", ".join(f"{k} ({', '.join(v['a'])})" for k, v in x["subdomains"].items())))
    if x.get("autoconfig"):
        ac = x["autoconfig"]
        servers = "; ".join(f"{s['dir']} {s['type']} {s['host']}:{s['port']} {s['socket']}" for s in ac["servers"])
        out.append(("autoconfig", f"{ac['source']} {ac.get('display_name', '')}: {servers}; domains: "
                                  f"{', '.join(ac['domains'][:20])}"))
    for p in x.get("imap", []):
        cert = p.get("cert") or {}
        out.append(("imap", f"{p['host']}:993 {p.get('banner') or p.get('error')} | cert {cert.get('issuer', '')} "
                            f"{cert.get('not_after', '')} valid={p.get('valid')} names={', '.join(cert.get('names', [])[:12])}"))
    if x.get("web_tls"):
        p, cert = x["web_tls"], x["web_tls"].get("cert") or {}
        out.append(("web tls", f"{p.get('error') or ''}{cert.get('issuer', '')} until {cert.get('not_after', '')} "
                               f"valid={p.get('valid')} {p.get('invalid_reason', '')} names={', '.join(cert.get('names', [])[:12])}"))
    crt = x.get("crtsh") or {}
    if crt:
        out.append(("names in TLS certificates", ", ".join(sorted({registrable_of(n) for p in x.get("imap", []) + [x.get("web_tls") or {}]
                                                                 for n in (p.get("cert") or {}).get("names", [])}))[:500]))
        mailish = [n for n in crt.get("names", []) if re.search(r"mail|imap|smtp|pop|mx|autoconfig|webmail", n)]
        out.append(("crt.sh", crt.get("error") or f"{crt.get('certs')} certs {crt.get('first')}–{crt.get('last')}, "
                                                  f"{len(crt.get('names', []))} names; mail-related: {', '.join(mailish[:15])}"))
    if x.get("wayback"):
        out.append(("wayback", json.dumps(x["wayback"], ensure_ascii=False)))
    for key in ("checkhost_http", "checkhost_imap"):
        ch = x.get(key)
        if ch:
            out.append((key, ch.get("error") or f"{ch['ok']}/{ch['total']} ok {ch['report']} " +
                        "; ".join(f"{c['country']}: {c['msg']}" for c in ch["checks"])))
    return out


# --------------------------------------------------------------------------- main
def main():
    domains = []
    for line in open(DOMAINS_TSV, encoding="utf-8"):
        d, _, s = line.rstrip("\n").partition("\t")
        if d:
            domains.append((d, s == "1"))
    info = json.load(open(os.path.join(DNS_DIR, "ipinfo.json"), encoding="utf-8"))
    dns = {d: json.load(open(os.path.join(DNS_DIR, d + ".json"), encoding="utf-8")) for d, _ in domains}
    web, deep = {}, {}
    for d, _ in domains:
        p = os.path.join(WEB_DIR, d, "meta.json")
        web[d] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        p = os.path.join(DEEP_DIR, d + ".json") if DEEP_DIR else ""
        deep[d] = json.load(open(p, encoding="utf-8")) if p and os.path.exists(p) else {}

    # 2nd-domain candidates: keep only those whose MX points at the same mail infrastructure
    # sources: <option> lists and @domain mentions on the site, the domains in the mail client autoconfig,
    # and the names covered by the IMAP / website TLS certificates
    cand, cert_cand = {}, {}
    for d, _ in domains:
        w, r, x = web[d], dns[d], deep[d]
        names = set(w.get("option_domains", [])) | set(w.get("at_domains", []))
        names |= set((x.get("autoconfig") or {}).get("domains", []))
        certs = set()
        for probe in x.get("imap", []) + [x.get("web_tls") or {}]:
            certs |= {registrable_of(n) for n in (probe.get("cert") or {}).get("names", [])}
        skip = {d, r["base"]}
        cand[d] = sorted(n for n in names if n not in skip and not PLACEHOLDER.search(n))
        cert_cand[d] = sorted(n for n in certs - names if n not in skip and not PLACEHOLDER.search(n))
        cert_cand[d] = [n for n in cert_cand[d] if n not in {registrable_of(h) for _, h in r.get("mx", [])}]
    lookups = sorted({n for v in cand.values() for n in v})
    cache_path = os.path.join(DNS_DIR, "candidate_mx.json")
    mx_cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
    todo = [n for n in lookups if n not in mx_cache]
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        for n, hosts in zip(todo, pool.map(dig_mx, todo)):
            mx_cache[n] = hosts
    json.dump(mx_cache, open(cache_path, "w", encoding="utf-8"), indent=1)
    ip_path = os.path.join(DNS_DIR, "candidate_mx_ips.json")
    ip_cache = json.load(open(ip_path, encoding="utf-8")) if os.path.exists(ip_path) else {}
    todo = sorted({h for hosts in mx_cache.values() for h in hosts[:2]} - set(ip_cache))
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        for h, ips in zip(todo, pool.map(dig_a, todo)):
            ip_cache[h] = ips
    json.dump(ip_cache, open(ip_path, "w", encoding="utf-8"), indent=1)

    def cname(i):
        return "Netherlands" if i.get("country") == "The Netherlands" else i.get("country")

    def ipline(ip, label):
        i = info.get(ip, {})
        if i.get("status") != "success":
            return f"{label} | ? "
        asn, _, org = (i.get("as") or "").partition(" ")
        return f"{label} | {flag(i.get('countryCode'))} | {cname(i)} | {asn} | {org or i.get('org') or ''}".rstrip()

    header = emcdata.HEADER
    overrides = json.load(open(OVERRIDES_PATH, encoding="utf-8")) if os.path.exists(OVERRIDES_PATH) else {}
    rows, review = [], []
    for d, struck in domains:
        r, w = dns[d], web[d]
        reg = r.get("reg", {})
        wdir = os.path.join(WEB_DIR, d)
        home = read(os.path.join(wdir, "home.txt"))
        texts = {c: read(os.path.join(wdir, c + ".txt"))
                 for c in ("tos", "privacy", "imprint", "pricing", "signup", "domains", "service")}
        pages = w.get("pages", {})

        # servers
        a_lines = [ipline(ip, ip) for ip in r.get("a", [])[:2]]
        mx_lines, mx_ccs, mx_hosts, mx_ips = [], [], set(), set()
        for host in list(dict.fromkeys(h for _, h in r.get("mx", [])))[:4]:
            ips = r.get("mx_ips", {}).get(host, [])
            mx_hosts.add(host)
            mx_ips.update(ips)
            if ips:
                mx_lines.append(ipline(ips[0], host))
                cc = info.get(ips[0], {}).get("countryCode")
                if cc and cc not in mx_ccs:
                    mx_ccs.append(cc)
            else:
                mx_lines.append(f"{host} | (no A record)")
        if r.get("mx_null"):
            mx_lines.insert(0, "null MX (0 .): domain does not accept mail")
        a_ccs = [info.get(ip, {}).get("countryCode") for ip in r.get("a", [])[:2]]
        juris_ccs = mx_ccs or [c for c in dict.fromkeys(a_ccs) if c]
        names = {v.get("countryCode"): cname(v) for v in info.values()}
        if len(juris_ccs) == 1:
            jurisdiction = names.get(juris_ccs[0], juris_ccs[0])
        else:
            jurisdiction = "\n".join(f"{flag(c)} {names.get(c, c)}" for c in juris_ccs)

        # owner / flag
        reg_org = reg.get("registrant_org") if reg.get("registrant_org") and not PRIVACY_ORG.search(reg["registrant_org"]) else ""
        reg_cc = reg.get("registrant_cc") if reg.get("registrant_cc") and (reg_org or not reg.get("registrant_org")) else ""
        tld = r["base"].rsplit(".", 1)[-1]
        asn_orgs = [info.get(ip, {}).get("as") for ip in list(r.get("a", [])) + sorted(mx_ips)]
        owner = reg_org or pick_owner(home, texts["imprint"], texts["tos"], texts["privacy"], asn_orgs)
        if owner and not reg_org and brand_only(owner, d):
            owner = ""
        form_cc = next((cc for rx, cc in FORM_COUNTRY if owner and re.search(rx, owner)), "")
        # a registrant country is trustworthy when the registrant organisation is public too
        svc_cc = ((reg_cc if reg_org else "") or NATIONAL.get(tld) or form_cc or reg_cc
                  or imprint_country(texts["imprint"]) or (juris_ccs[0] if juris_ccs else ""))
        if owner and svc_cc:
            owner = f"{flag(svc_cc)} {owner}"

        # 2nd domains
        main_bases = {base2(h) for h in mx_hosts if not BIG_MX.search(h)}
        seconds = []
        if d != r["base"] and d.split(".")[0] in ("mail", "webmail", "email"):  # mail.sapo.pt -> sapo.pt addresses
            seconds.append(r["base"])
        for n in cand[d]:
            hosts = mx_cache.get(n, [])
            cand_ips = {ip for h in hosts[:2] for ip in ip_cache.get(h, [])}
            if set(hosts) & mx_hosts or ({base2(h) for h in hosts if not BIG_MX.search(h)} & main_bases) \
                    or (cand_ips & mx_ips and not any(BIG_MX.search(h) for h in hosts)):
                seconds.append(n)

        # website
        title = (w.get("title") or "").strip()
        meta = w.get("meta") or {}
        desc = (meta.get("description") or meta.get("og:description") or meta.get("twitter:description") or "").strip()
        blocked = bool(title and CHALLENGE.search(title)) or (w.get("home_ok") is False and bool(title))
        if not desc and texts["service"]:
            # no meta description: use the opening sentences of the provider's e-mail page instead
            lines = [l for l in texts["service"].splitlines() if len(l.split()) >= 3]
            desc = " ".join(lines)[:300]
            cut = desc.rfind(". ")
            desc = desc[:cut + 1] if cut > 80 else desc.rsplit(" ", 1)[0]
        site_desc = "" if blocked or not w.get("home_ok") else "\n".join(x for x in (title, desc) if x)

        def with_src(summary, cat):
            url = (pages.get(cat) or {}).get("final_url") or (pages.get(cat) or {}).get("url")
            if not texts[cat] or not (pages.get(cat) or {}).get("chars"):
                return ""
            return "\n".join(x for x in (summary or "Page found, no key clauses detected", url) if x)

        tos = with_src(summarize_tos(texts["tos"]), "tos")
        if not tos and summarize_tos(texts["service"]):  # rules stated on the e-mail page itself
            tos = with_src(summarize_tos(texts["service"]), "service")
        priv = with_src(summarize_privacy(texts["privacy"]), "privacy")
        price = summarize_price(home, texts["pricing"] + "\n" + texts["service"], texts["signup"],
                                (pages.get("pricing") or {}).get("url"))

        # note
        note = [f"Auto-collected {TODAY} (DNS, RDAP/whois, website); registration not tested."]
        if struck:
            note.append("Struck out in README.")
        if r["status"] == "NXDOMAIN":
            note.append("Domain does not resolve (NXDOMAIN).")
        elif r["status"] not in ("NOERROR",):
            note.append(f"DNS lookup failed ({r['status']}).")
        if reg.get("rdap_http") == 404 or reg.get("whois_not_found"):
            note.append("Domain is not registered.")
        if not r.get("mx") and not r.get("mx_null") and r["status"] == "NOERROR":
            note.append("No MX record: domain cannot receive mail.")
        if r.get("mx_source") and r["mx_source"] != d:
            note.append(f"MX taken from {r['mx_source']}.")
        x = deep[d]
        live = w.get("live") or {}
        state = w.get("live_state") or ("ok" if w.get("home_ok") else "blocked" if blocked else "unreachable")
        archive = w.get("archive")
        if r["status"] == "NOERROR":
            if state == "blocked":
                note.append("Website blocks automated access (bot check).")
            elif state in ("unreachable", "http-error"):
                err = next((t.get("error") or f"HTTP {t.get('status')}" for t in w.get("tried", [])
                            if t.get("error") or t.get("status")), "")
                ltitle = (live.get("title") or "").strip()
                if ltitle and state == "http-error":
                    err = f"{err}: \"{ltitle[:80]}\""
                note.append(f"Website unreachable ({err})." if err else "Website unreachable.")
            elif state in ("parked", "maintenance"):
                shown = (live.get("title") or "").strip() or read(os.path.join(wdir, "live.txt"), 400).strip()
                shown = " ".join(shown.split())[:90]
                note.append(("Domain is parked, for sale or not configured" if state == "parked" else "Website shows")
                            + (f": \"{shown}\"." if shown else "."))
            elif MAINTENANCE.search(title):
                note.append(f"Website shows: \"{title}\".")
            elif (len(home) > 200 and not MAIL_WORDS.search(home + " " + title) and not texts["service"]
                  and not w.get("webmail")):
                note.append("Homepage does not mention e-mail.")
            ch = x.get("checkhost_http") or {}
            if state in ("unreachable", "http-error") and ch.get("total"):
                where = ", ".join(dict.fromkeys(c["country"] for c in ch["checks"] if c["ok"] == bool(ch["ok"])))
                note.append(f"Also unreachable from {ch['total']} check-host.net locations ({where})." if not ch["ok"]
                            else f"Reachable from {ch['ok']} of {ch['total']} check-host.net locations ({where}): "
                                 f"the site blocks some networks.")
            final = live.get("final_url") or ""
            final_host = re.sub(r"^https?://([^/:]+).*$", r"\1", final).lower()
            if final_host and registrable_of(final_host) not in (r["base"], registrable_of(d)):
                how = {"meta-refresh": " (meta refresh)", "javascript": " (JavaScript)"}.get(
                    next((h.get("kind") for h in live.get("chain", []) if h.get("to")), ""), "")
                if len(final) > 60:
                    final = re.sub(r"^(https?://[^/]+/).*$", r"\1", final)
                note.append(f"Website redirects to {final}{how}.")
            if (x.get("web_tls") or {}).get("valid") is False:
                note.append(f"Website TLS certificate not trusted ({x['web_tls'].get('invalid_reason', '?').rstrip('.')}).")
        wb = x.get("wayback") or {}
        created = reg.get("created") or ""
        if created and wb.get("first") and created[:4].isdigit() and int(created[:4]) - int(wb["first"][:4]) >= 2:
            note.append(f"Domain registration dates from {created}, but the Wayback Machine has captures since "
                        f"{wb['first']}: the domain was registered before, possibly by someone else.")
        spam = sorted({m.lower() for m in SPAM.findall(home)}) if state == "ok" else []
        if spam:
            note.append(f"Homepage contains unrelated spam ({', '.join(spam[:3])}): the site may be hacked or abandoned.")
        if archive:
            note.append(f"Website text taken from the Wayback Machine copy of {short_date(archive['timestamp'])}"
                        f" ({archive.get('url')}).")
        if (r["status"] != "NOERROR" or state != "ok") and (wb.get("first") or wb.get("last")):
            note.append(f"Wayback Machine captures: {wb.get('first', '?')} to {wb.get('last', '?')}.")
        crt = x.get("crtsh") or {}
        if r["status"] != "NOERROR" and crt.get("last"):
            note.append(f"Last TLS certificate issued in {crt['last']} (crt.sh).")
        # mail servers
        hosted = next((name for name, rx in MAIL_HOSTS for h in mx_hosts if re.search(rx, h)), "")
        if hosted:
            note.append(f"Mail servers belong to {hosted}.")
        imap = x.get("imap") or []
        imap_ok = next((p for p in imap if (p.get("banner") or "").startswith("* OK")), None)
        if imap_ok:
            soft = f" ({imap_ok['software']})" if imap_ok.get("software") else ""
            bad = "" if imap_ok.get("valid", True) else \
                f", certificate not trusted: {(imap_ok.get('invalid_reason') or '?').rstrip('.')}"
            note.append(f"IMAP server {imap_ok['host']}:993 answers{soft}{bad}.")
        elif imap and r.get("mx") and not hosted:  # a mail hosting company's MX says nothing about IMAP
            ci = x.get("checkhost_imap") or {}
            extra = ""
            if ci.get("total"):
                extra = (f", nor from {ci['total']} check-host.net locations" if not ci["ok"]
                         else f", but the port is open from {ci['ok']} of {ci['total']} check-host.net locations")
            note.append(f"IMAP (port 993) does not answer on {', '.join(p['host'] for p in imap[:3])}{extra}.")
        ac = x.get("autoconfig")
        if ac:
            note.append("Listed in Thunderbird's ISPDB." if ac["source"] == "Thunderbird ISPDB"
                        else "Publishes mail client autoconfig.")
        if w.get("webmail"):
            wm = w["webmail"]
            url = wm["url"].split("?")[0]
            if not re.match(r"^https?://[^/]+/(?:(?:webmail|mail|roundcube|sogo)/?)?$", url):
                url = re.sub(r"^(https?://[^/]+/).*$", r"\1", url)  # drop session tokens and deep paths
            note.append(f"Webmail: {url}" + (f" ({wm['software']})." if wm.get("software") else "."))
        if r["status"] == "NOERROR" and r.get("mx") and x:
            sec = []
            if x.get("spf"):
                sec.append("SPF")
            m = re.search(r"\bp=(\w+)", x.get("dmarc") or "", re.I)
            if m:
                sec.append(f"DMARC p={m.group(1).lower()}")
            sec += [name for name, key in (("MTA-STS", "mta_sts"), ("DANE", "dane"), ("DNSSEC", "dnssec")) if x.get(key)]
            note.append("Mail DNS: " + (", ".join(sec) if sec else "no SPF, DMARC, MTA-STS, DANE or DNSSEC") + ".")
        # only the provider's own marketing/signup pages, not legal texts that list prohibited uses
        claim_text = "\n".join((home, texts["signup"], texts["pricing"], texts["domains"], texts["service"]))
        claims = []
        for label, rx in CLAIMS:
            if label.startswith("no "):  # the phrase itself is a negation ("no phone number required")
                if re.search(rx, claim_text, re.I):
                    claims.append(label)
            elif polarity(claim_text, rx, min_words=1) == "pos":  # skip "we can't accept cryptocurrency"
                claims.append(label)
        if claims:
            note.append("Site mentions: " + ", ".join(claims) + ".")
        if w.get("onion"):
            note.append("Tor: " + w["onion"][0] + ".")

        row = OrderedDict()
        row[header[0]] = flag(svc_cc)
        row[header[1]] = d
        row[header[2]] = "\n".join(seconds)
        row[header[3]] = "\n".join(a_lines)
        row[header[4]] = "\n".join(mx_lines)
        row[header[5]] = reg.get("created") or ""
        row[header[6]] = reg.get("expires") or ""
        row[header[7]] = owner
        row[header[8]] = alliance(juris_ccs)
        row[header[9]] = jurisdiction
        row[header[10]] = site_desc
        row[header[11]] = tos
        row[header[12]] = priv
        row[header[13]] = ""
        row[header[14]] = ""
        row[header[15]] = price
        row[header[16]] = ""
        row[header[17]] = " ".join(note)
        for col, value in overrides.get(d, {}).items():
            key = next(h for h in header if h.splitlines()[0].strip() == col)
            row[key] = f"{row[key]} {value}".strip() if col == "Note" else value
        rows.append(row)
        review.append((d, row, cand[d]))

    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def q(v):
        if v is None or v == "":
            return ""
        if date_re.match(v):
            return v
        return '"' + v.replace('"', '""') + '"'

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(q(h) for h in header) + "\n")
        for row in rows:
            f.write(",".join(q(v) for v in row.values()) + "\n")

    with open(REVIEW_MD, "w", encoding="utf-8") as f:
        for d, row, cands in review:
            f.write(f"## {d}\n")
            for k, v in row.items():
                if v:
                    f.write(f"- **{k.splitlines()[0]}**: {v.replace(chr(10), ' ⏎ ')}\n")
            if cands:
                f.write(f"- _2nd-domain candidates_: {', '.join(cands[:30])}\n")
            for line in review_details(web[d], deep[d]):
                f.write(f"- _{line[0]}_: {line[1]}\n")
            f.write("\n")
    filled = {h.splitlines()[0]: sum(1 for row in rows if row.get(h)) for h in header}
    drafts = os.path.join(os.path.dirname(OUT_CSV), "records")
    os.makedirs(drafts, exist_ok=True)
    zero_width = "\u200b\u200c\u200d\u2060\ufeff"
    for row in rows:
        cells = [(row.get(h) or "").strip(zero_width + " \n\t") for h in header]
        rec = emcdata.from_row(cells, "pending", False, 1)
        emcdata.save(rec, drafts)
    print(f"rows: {len(rows)}; draft records: {drafts}/ (add them with merge.py add)")
    print("filled:", json.dumps(filled, ensure_ascii=False))


if __name__ == "__main__":
    main()

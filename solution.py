"""Knowledge system for a credit-union website that changes over time.

Stdlib only. No network, no API keys, no randomness.
See NOTES.md for the design.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Optional

try:  # use the harness types when available, else identical local copies
    from contract import Answer, Conflict, Source  # type: ignore
except Exception:  # pragma: no cover
    class Source:
        def __init__(self, url, snapshot_id=None, quote=None):
            self.url, self.snapshot_id, self.quote = url, snapshot_id, quote

    class Answer:
        def __init__(self, text, sources=None, confident=True, conflict_note=None):
            self.text, self.sources = text, sources or []
            self.confident, self.conflict_note = confident, conflict_note

    class Conflict:
        def __init__(self, topic, values, sources, resolution=None, reason=None):
            self.topic, self.values, self.sources = topic, values, sources
            self.resolution, self.reason = resolution, reason

# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------
STOP = set("""a an the of to in on for and or is are was were be been do does did can could would should will
i me my we our you your us it its this that these those with at by from as about any what which who whom how
when where why there here have has had not no than then so if into out up down over under per tell give get
please know like need want just also me link url use see does doe many much repeat tell right now currently current""".split())

SYN = {
    "cd": "certificate", "cds": "certificate", "certificates": "certificate",
    "mortgage": "home", "mortgages": "home",
    "interest": "rate", "rates": "rate", "dividend": "rate",
    "apy": "apy", "apr": "apr",
    "cards": "card", "branches": "branch", "locations": "branch", "location": "branch",
    "phone": "phone", "number": "number",
    "hours": "hour", "open": "hour", "opens": "hour", "eligible": "join", "eligibility": "join", "wallet": "wallet", "wallets": "wallet",
}

LINK_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MDLINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
FOOT_RE = re.compile(r"(%|APR|APY|apr|apy)\s?\d\b(?![\d.,%])")
DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def clean(t: str) -> str:
    t = LINK_RE.sub("", t)
    t = MDLINK_RE.sub(r"\1", t)
    t = t.replace("\\r\\n", " ").replace("\\*", "*").replace("\\\\", " ")
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"<br\s*/?>", " ", t)
    t = FOOT_RE.sub(r"\1", t)
    t = re.sub(r"(\d)\*+", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def stem(w: str) -> str:
    return _stem(w)[:7] if len(_stem(w)) > 8 else _stem(w)


def _stem(w: str) -> str:
    if w in SYN:
        return SYN[w]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def norm_numbers(t: str) -> str:
    t = t.lower()
    t = re.sub(r"(\d)\s*[-\s]\s*(month|year|yr|mo)s?\b", r"\1\2", t)
    t = t.replace("yr", "year")
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = re.sub(r"\$\s+", "$", t)
    t = t.replace("7/1", "seven1arm")
    return t


TOK_RE = re.compile(r"[a-z0-9$%.#]+")


def tokens(t: str) -> list:
    out = []
    for w in TOK_RE.findall(norm_numbers(t)):
        w = w.strip(".")
        if not w:
            continue
        if "." in w and not re.match(r"^\$?\d", w):
            parts = [p for p in w.split(".") if p]
        else:
            parts = [w]
        for p in parts:
            p = p.rstrip("%") if p.endswith("%") and False else p
            p = stem(p)
            if p and p not in STOP:
                out.append(p)
    return out


def money_vals(t: str) -> list:
    t = norm_numbers(t)
    return [float(m) for m in re.findall(r"\$(\d+(?:\.\d+)?)", t)]


def authority(url: str) -> float:
    u = url.lower()
    if "/tag/" in u or "/author" in u or "?" in u and "privacy" in u:
        return 0.12
    if "offer-expired" in u:
        return 0.15
    if "info.dupaco.com" in u or "live-dupaco" in u or "pantheonsite" in u:
        return 0.2
    if "/calculators" in u or "calculator" in u or "business-calculators" in u:
        return 0.3
    if "/learn" in u or "dupacodaily" in u or re.search(r"/20\d\d/", u) or "/business-resources" in u or "business/resources" in u:
        return 0.35
    if "/newsroom" in u:
        return 0.5
    if "-lp" in u or "/earn-more" in u or "switch-and-earn" in u:
        return 0.7
    if "/hubfs/" in u:
        return 0.75
    if re.search(r"/rates/?$|business-rates", u):
        return 1.0
    if re.search(r"/personal/|/business/|/checking$|/contact|/loans$|/personal-loans|/about|/membership", u):
        return 0.95
    if u.rstrip("/").count("/") <= 2:
        return 0.7
    return 0.65


# --------------------------------------------------------------------------
# chunking
# --------------------------------------------------------------------------
class Chunk:
    def __init__(self, cid, url, kind, display, ctx, raw, toks, auth, snaps, label="", values=(), eff=""):
        self.cid, self.url, self.kind, self.display, self.ctx, self.raw = cid, url, kind, display, ctx, raw
        self.toks, self.auth, self.snaps, self.label, self.values, self.eff = toks, auth, snaps, label, values, eff
        self.tf = {}
        self.length = 0
        self.sections = ()


def split_row(line: str) -> list:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [clean(c) for c in re.split(r"(?<!\\)\|", line)]


def sentences(par: str) -> list:
    if len(par) <= 500:
        return [par]
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", par):
        if len(cur) + len(s) > 450 and cur:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return out


def parse_page(md: str):
    """Yield (kind, ctx, display, raw, label, values, eff)."""
    lines = md.split("\n")
    heading = ""
    pre = ""
    i = 0
    n = len(lines)
    para = []
    out = []
    eff_default = ""

    def flush():
        nonlocal para, pre
        if para:
            txt = clean(" ".join(para))
            if len(txt) > 2:
                short = len(txt) < 70 and not txt.endswith((".", "!", "?"))
                for sx in sentences(txt):
                    pf = pre or (heading if len(heading) < 60 else "")
                    d = sx if (short or not pf or sx.lower().startswith(pf.lower()[:20])) else f"{pf}: {sx}"
                    out.append(("text", (heading + " " + pre).strip(), d, sx, "", (), ""))
                if short:
                    pre = txt.rstrip(":")
            para = []

    while i < n:
        ln = lines[i].rstrip()
        if ln.startswith("|"):
            flush()
            tbl = []
            while i < n and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            hdr = None
            rows = []
            for k, r in enumerate(tbl):
                cells = split_row(r)
                if all(re.match(r"^:?-{2,}:?$", c.strip()) or c.strip() == "" for c in cells) and any(
                        c.strip() for c in cells):
                    continue
                if hdr is None:
                    hdr = cells
                else:
                    rows.append(cells)
            # effective date from following lines
            eff = ""
            for j in range(i, min(i + 6, n)):
                m = re.search(r"effective (?:as of|on)?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", lines[j], re.I)
                if m:
                    y = int(m.group(3))
                    y = y + 2000 if y < 100 else y
                    eff = "%04d-%02d-%02d" % (y, int(m.group(1)), int(m.group(2)))
                    break
            if hdr and rows:
                ctx = (heading + " " + pre).strip()
                for cells in rows:
                    if not any(cells):
                        continue
                    parts = []
                    for hh, cc in zip(hdr, cells):
                        if not cc:
                            continue
                        parts.append(f"{hh}: {cc}" if hh else cc)
                    parts = [x for x in parts if not re.search(r"payment example|^\W*$", x, re.I)]
                    disp = "; ".join(parts)
                    labelcells = [c for c in cells if c and not re.search(r"\d+(\.\d+)?\s*%|^\$?\s*[\d,.]+$", c)]
                    label = norm_numbers(" ".join(labelcells[:2]))
                    vals = tuple(re.findall(r"\d+(?:\.\d+)?%", " ".join(cells)))
                    out.append(("row", ctx, disp, tuple(zip(hdr, cells)), label, vals, eff))
            continue
        s = ln.strip()
        if not s:
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            flush()
            heading = clean(m.group(2))
            pre = ""
            if len(heading) > 2:
                out.append(("text", "", heading, heading, "", (), ""))
            i += 1
            continue
        if s.startswith(("* * *", "---")):
            flush()
            i += 1
            continue
        if re.match(r"^([-*+]|\d+\.)\s+", s):
            flush()
            para = [re.sub(r"^([-*+]|\d+\.)\s+", "", s)]
            flush()
            i += 1
            continue
        if s.startswith("![") and s.endswith(")"):
            i += 1
            continue
        para.append(s)
        i += 1
    flush()
    return out


# --------------------------------------------------------------------------
# knowledge system
# --------------------------------------------------------------------------
class KS:
    def __init__(self):
        self.snaps = []            # list of (snapshot_id, taken_on)
        self.chunks = []
        self.by_key = {}           # (url, display) -> cid
        self.inv = defaultdict(list)   # tok -> [cid]
        self.page_titles = {}
        self.page_snaps = defaultdict(set)   # url -> snapshot idxs
        self.dirty = True
        self.avglen = 1.0
        self.N = 1
        self.df = {}
        self._cache = {}
        self._conf = None
        self._ptoks = {}
        self.seqs = {}

    # ------------------------------ ingest
    def ingest(self, snapshot):
        idx = len(self.snaps)
        self.snaps.append((snapshot.snapshot_id, snapshot.taken_on))
        for p in snapshot.pages:
            self.page_titles[p.url] = p.title
            self.page_snaps[p.url].add(idx)
            a = authority(p.url)
            title = clean(p.title or "")
            order = []
            for kind, ctx, disp, raw, label, vals, eff in parse_page(p.markdown):
                key = (p.url, disp, ctx)
                cid = self.by_key.get(key)
                if cid is None:
                    cid = len(self.chunks)
                    toks = tokens(ctx + " " + disp) + tokens(title)[:6]
                    ch = Chunk(cid, p.url, kind, disp, ctx, raw, toks, a, set(), label, vals, eff)
                    self.chunks.append(ch)
                    self.by_key[key] = cid
                self.chunks[cid].snaps.add(idx)
                order.append(cid)
            self.seqs[(p.url, idx)] = order
            hd = ""
            for line in p.markdown.split("\n"):
                hm = re.match(r"^#{1,6}\s+(.*)", line)
                if hm:
                    hd = clean(hm.group(1))
                for m in MDLINK_RE.finditer(line):
                    anchor, tgt = clean(m.group(1)), m.group(2).strip().split(" ")[0]
                    if not tgt.startswith("http") or len(anchor) < 3 or tgt.lower().endswith((".jpeg", ".jpg", ".png", ".svg", ".webp")):
                        continue
                    key = ("LINK", anchor.lower(), tgt, hd)
                    cid = self.by_key.get(key)
                    if cid is None:
                        cid = len(self.chunks)
                        ch = Chunk(cid, tgt, "link", anchor, hd, tgt,
                                   tokens(anchor) + tokens(hd) + tokens(re.sub(r"[/_\-]", " ", tgt.split("//")[-1])),
                                   authority(tgt), set())
                        ch.sections = set()
                        self.chunks.append(ch)
                        self.by_key[key] = cid
                    self.chunks[cid].snaps.add(idx)
                    self.chunks[cid].sections.add(p.url)
        self.dirty = True
        self._cache = {}
        self._conf = None

    def _build(self):
        if not self.dirty:
            return
        self.inv = defaultdict(list)
        total = 0
        for ch in self.chunks:
            tf = defaultdict(int)
            for t in ch.toks:
                tf[t] += 1
            ch.tf = tf
            ch.length = len(ch.toks)
            total += ch.length
            for t in tf:
                self.inv[t].append(ch.cid)
        self.N = max(1, len(self.chunks))
        self.avglen = total / self.N if self.N else 1.0
        self.df = {t: len(v) for t, v in self.inv.items()}
        self.seq = {}
        for ch in self.chunks:
            pass
        self.keyidx = defaultdict(list)
        for ch in self.chunks:
            if ch.kind == "row" and ch.label:
                self.keyidx[self._fkey(ch)].append(ch.cid)
        self.dirty = False

    def idf(self, t):
        d = self.df.get(t, 0)
        return math.log(1 + (self.N - d + 0.5) / (d + 0.5))

    # ------------------------------ snapshot selection
    def snap_for(self, as_of):
        if not self.snaps:
            return -1
        if not as_of:
            return len(self.snaps) - 1
        d = str(as_of)[:10]
        best = 0
        for i, (_, t) in enumerate(self.snaps):
            if t[:10] <= d:
                best = i
        return best

    # ------------------------------ retrieval
    def retrieve(self, question, si, want_link=False, topn=80):
        ql = question.lower()
        qt = tokens(question)
        uq = []
        for t in qt:
            if t not in uq:
                uq.append(t)
        qamounts = money_vals(question)
        qyears = [int(y) for y in re.findall(r"\b(20\d\d)\b", question)]
        scores = defaultdict(float)
        k1, b = 1.2, 0.6
        for t in uq:
            w = self.idf(t)
            for cid in self.inv.get(t, ()):
                ch = self.chunks[cid]
                if si not in ch.snaps:
                    continue
                if (ch.kind == "link") != want_link:
                    continue
                f = ch.tf[t]
                scores[cid] += w * f * (k1 + 1) / (f + k1 * (1 - b + b * ch.length / self.avglen))
        cand = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:250]
        pairs = [(qt[i], qt[i + 1]) for i in range(len(qt) - 1)]
        wtot = sum(self.idf(t) for t in uq) or 1.0
        biz_q = "business" in ql or "commercial" in ql
        promo_q = bool(re.search(r"special|promo|intro|offer|relief|bonus|expired", ql))
        res = []
        want_rate = bool(re.search(r"\b(rate|apr|apy|interest|dividend)s?\b", ql))
        f_route = bool(re.search(r"routing|\baba\b", ql))
        f_phone = bool(re.search(r"phone|call|telephone|contact", ql))
        f_hours = bool(re.search(r"hour|open|clos|lobby|drive", ql))
        f_count = bool(re.search(r"how many|number of|count", ql))
        mcnt = re.search(r"how many ([a-z]+)", ql)
        cnt_noun = (stem(mcnt.group(1))[:6] if mcnt else "")
        f_addr = bool(re.search(r"address|located|location of", ql))
        f_when = bool(re.search(r"\bwhen\b|what date|which year|last year", ql))
        for cid, s in cand:
            ch = self.chunks[cid]
            adj = s
            d = ch.display
            if want_rate and ch.kind != "link":
                if ch.kind == "row":
                    if not re.search(r"\d\s*%", d) or not re.search(r"rate|apr|apy|yield|dividend|interest", d, re.I):
                        continue
                elif not re.search(r"(rate|apr|apy|yield)\b[^.%]{0,50}\d+(\.\d+)?\s*%|\d+(\.\d+)?\s*%[^.%]{0,25}(rate|apr|apy)", ch.raw, re.I):
                    continue
            if f_route and re.search(r"\b\d{9}\b", d):
                adj *= 2.0
            if f_phone and re.search(r"\d{3}[-. ]\d{3}[-. ]\d{4}", d):
                adj *= 1.5
            if f_hours and re.search(r"\b\d{1,2}(:\d\d)?\s*[ap]\.?m", d, re.I):
                adj *= 2.0
            if f_count and cnt_noun and re.search(r"\b\d+\s+(?:[a-z]+\s+){0,2}" + cnt_noun, ch.raw, re.I):
                adj *= 3.0
            if f_addr and re.search(r"\b\d{5}\b", d):
                adj *= 1.5
            if f_when and re.search(r"\b(20\d\d|january|february|march|april|may|june|july|august|september|october|november|december)\b", d, re.I):
                adj *= 1.3
            if pairs:
                tk = ch.toks
                bg = set(zip(tk, tk[1:]))
                hit = sum(1 for p in pairs if p in bg)
                adj *= 1 + 0.35 * hit
            m = re.search(r"-(\d)/?$", ch.url)
            if m and "/newsroom/" in ch.url:
                adj *= 1 + 0.12 * int(m.group(1))
            ptoks = self.page_toks(ch.url)
            pcov = sum(self.idf(t) for t in uq if t in ptoks) / wtot
            adj *= 1 + 0.6 * pcov
            a = ch.auth
            adj *= (0.2 + 0.8 * a) ** 1.5
            u = ch.url.lower()
            isbiz = self._isbiz(ch)
            if not biz_q and isbiz:
                adj *= 0.6
            if biz_q and not isbiz and "/personal" in u:
                adj *= 0.6
            if not promo_q and ch.kind == "row" and re.search(r"relief program|special|introductory|promo|bonus", ch.display + " " + ch.ctx, re.I):
                adj *= 0.7
            if ch.kind == "row":
                if self._beaten(ch, si):
                    continue
                adj *= 1.15 * (1.3 if want_rate else 1.0)
                if "refinanc" in d.lower() and "refinanc" not in ql:
                    adj *= 0.7
                if "purchase" in d.lower() and "refinanc" in ql:
                    adj *= 0.7
                if qamounts:
                    adj *= self._amount_factor(ch, qamounts)
                if qyears:
                    adj *= self._year_factor(ch, qyears)
            elif ch.kind == "link":
                adj *= 1 + 0.3 * math.log(1 + len(ch.sections))
                if isbiz and not biz_q:
                    adj *= 0.7
                if re.search(r"online|apply|application|sign up|enroll", ql) and re.match(r"https?://(?!www\.)[a-z]+\.dupaco\.com", ch.raw):
                    adj *= 1.8
            res.append((adj, cid))
        res.sort(key=lambda x: (-x[0], x[1]))
        return uq, res[:topn]

    def page_toks(self, url):
        pt = self._ptoks.get(url)
        if pt is None:
            pt = set(tokens(re.sub(r"[/_\-]", " ", url.split("dupaco.com")[-1]) + " " + self.page_titles.get(url, "")))
            self._ptoks[url] = pt
        return pt

    def _amount_factor(self, ch, amts):
        txt = norm_numbers(ch.display)
        rng = re.findall(r"\$(\d+(?:\.\d+)?)\s*(?:-|–|to|and)\s*\$(\d+(?:\.\d+)?)", txt)
        plus = re.findall(r"\$(\d+(?:\.\d+)?)\s*\+", txt)
        orless = re.findall(r"\$(\d+(?:\.\d+)?)\s*or (?:less|under)", txt)
        f = 1.0
        for a in amts:
            hit = any(float(lo) - 0.005 <= a <= float(hi) + 0.995 for lo, hi in rng)
            hit = hit or any(a >= float(p) for p in plus)
            if hit:
                f *= 2.0
            elif rng or plus:
                f *= 0.4
        return f

    def _year_factor(self, ch, years):
        m = re.search(r"(20\d\d)\s*[-–]\s*(\d\d)\b", ch.display)
        if m:
            lo, hi = int(m.group(1)), 2000 + int(m.group(2))
            return 1.8 if any(lo <= y <= hi for y in years) else 0.4
        return 1.0

    # ------------------------------ answering
    def answer(self, question, as_of=None):
        self._build()
        if not self.snaps:
            return Answer(text="I don't know. Please contact us for details.", confident=False)
        if as_of and str(as_of)[:10] < self.snaps[0][1][:10]:
            return self._refuse()
        si = self.snap_for(as_of)
        key = (question, si)
        if key in self._cache:
            return self._cache[key]
        ans = self._answer(question, si)
        self._cache[key] = ans
        return ans

    def _refuse(self, why=""):
        return Answer(
            text="I don't know. That is not listed on our website. Please contact us for details.",
            sources=[], confident=False)

    CAPSKIP = set("dupaco i apy apr cd cds atm ach ira hsa heloc arm".split())
    MONTHS = set("january february march april may june july august september october november december monday tuesday wednesday thursday friday saturday sunday".split())

    def _entities(self, question):
        words = re.findall(r"[A-Za-z][A-Za-z$\-]*", question)
        out = []
        for k, w in enumerate(words):
            if k == 0 or not w[0].isupper() or w.isupper():
                continue
            lw = w.lower()
            if lw in self.CAPSKIP or lw in self.MONTHS:
                continue
            for t in tokens(w.replace("-", " ")):
                if t not in out:
                    out.append(t)
        return out

    @staticmethod
    def _feature_ok(ql, d):
        if re.search(r"hour|lobby|drive", ql) and not re.search(r"[ap]\.?m|closed|24", d, re.I):
            return False
        if re.search(r"routing|\baba\b", ql) and not re.search(r"\b\d{9}\b", d):
            return False
        return True

    def _live_has(self, tok, si, min_auth):
        for c in self.inv.get(tok, ()):
            ch = self.chunks[c]
            if si in ch.snaps and ch.auth >= min_auth and ch.kind != "link":
                return True
        return False

    def _answer(self, question, si):
        ql = question.lower()
        want_rate = bool(re.search(r"\b(rate|apr|apy|interest|dividend|cost of borrowing)s?\b", ql))
        want_link = bool(re.search(r"\b(link|url|web ?address|website address|webpage|web page)\b", ql)) or bool(
            re.search(r"how (?:do|can) i (?:join|apply|sign up|enroll|open)\b.*\bonline\b", ql))
        if want_link:
            return self._link_answer(question, si)
        uq, res = self.retrieve(question, si)
        if not res:
            return self._refuse()
        if want_rate:
            good = [(s, c) for s, c in res if self.chunks[c].auth >= 0.6]
            if good:
                res = good
        ents = self._entities(question)
        if ents:
            rare = max(ents, key=lambda e: (self.idf(e), e))
            if not self._live_has(rare, si, 0.0):
                return self._refuse()
            filt = [(s_, c) for s_, c in res if all(e in self.chunks[c].toks for e in ents)]
            if not filt:
                filt = [(s_, c) for s_, c in res if rare in self.chunks[c].toks]
            if filt:
                res = filt
        top_s, top_id = res[0]
        top = self.chunks[top_id]
        qset = set(uq)
        topset = set(top.toks)
        wtot = sum(self.idf(t) for t in qset) or 1.0
        minauth = 0.6 if want_rate else 0.0
        unseen = [t for t in qset if not self._live_has(t, si, minauth)]
        # coverage of the question by the best few passages together
        union = set()
        for _, c in res[:3]:
            union |= set(self.chunks[c].toks)
        cov = sum(self.idf(t) for t in qset if t in union) / wtot
        unseen_w = sum(self.idf(t) for t in unseen) / wtot
        if cov < 0.25 or (want_rate and unseen_w >= 0.3 and cov < 0.8):
            return self._refuse()
        if not self._feature_ok(ql, self.chunks[top_id].display):
            return self._refuse()
        if want_rate:
            tcov = sum(self.idf(t) for t in qset if t in topset) / wtot
            if tcov < 0.5:
                return self._refuse()

        want_list = bool(re.search(
            r"\b(all|list|options|what (?:credit )?cards|rates for|rates\b|certificates?|hours|offer|which)\b", ql))
        picked = [top_id]
        cutoff = 0.62 * top_s if (top.kind == "row" or want_list) else 0.78 * top_s
        for s, cid in res[1:]:
            if len(picked) >= (8 if (want_list or top.kind == "row") else 3):
                break
            ch = self.chunks[cid]
            if s < cutoff:
                break
            if top.kind == "row" and ch.kind != "row":
                continue
            if top.kind == "text" and not want_list and ch.url != top.url:
                continue
            if top.kind == "row" and ch.url != top.url and top.auth > ch.auth and self._same_fact(top, ch):
                continue
            if ch.display in [self.chunks[p].display for p in picked]:
                continue
            if ch.kind == "row" and any(
                    self.chunks[p].kind == "row" and self._fkey(self.chunks[p]) == self._fkey(ch)
                    and self.chunks[p].values == ch.values for p in picked):
                continue
            picked.append(cid)
        picked = self._supersede(picked)
        if top.kind == "text" and not want_rate:
            picked = self._extend(picked, si)

        if want_rate:
            if not re.search(r"\d+(?:\.\d+)?\s*%", self.chunks[picked[0]].display):
                return self._refuse()
        lines, srcs, seen = [], [], set()
        for cid in picked:
            ch = self.chunks[cid]
            t = ch.display
            if ch.kind == "row" and ch.ctx:
                t = f"{ch.ctx[:70]} - {t}"
            if t in seen:
                continue
            seen.add(t)
            lines.append(t)
            urls = [ch.url]
            if ch.kind == "row" and ch.label:
                for o in self.keyidx.get(self._fkey(ch), ()):
                    oc = self.chunks[o]
                    if si in oc.snaps and oc.values == ch.values and oc.url not in urls:
                        urls.append(oc.url)
            for u in sorted(urls[1:], key=lambda x: (-authority(x), x))[:2] if False else urls[:3]:
                if u not in [x.url for x in srcs]:
                    srcs.append(Source(url=u, snapshot_id=self.snaps[si][0], quote=t[:160]))
        return Answer(text=" ".join(lines), sources=srcs[:6], confident=True)

    @staticmethod
    def _isbiz(ch):
        u = ch.url.lower()
        return "/business" in u or "business-rates" in u or "business" in ch.ctx.lower()[:60] or ch.display.lower().startswith("business")

    def _fkey(self, ch):
        return (re.sub(r"\d{4}\s*[-–]\s*\d{2}", "YR", ch.label), self._isbiz(ch))

    _factkey = _fkey

    def _beaten(self, ch, si):
        """True when another live page states the same labelled fact with a different value
        and wins on authority, then effective date."""
        mine = (ch.auth, ch.eff)
        for o in self.keyidx.get(self._fkey(ch), ()):
            oc = self.chunks[o]
            if oc.url != ch.url and si in oc.snaps and oc.values != ch.values and (oc.auth, oc.eff) > mine:
                return True
        return False

    def _same_fact(self, a, b):
        return a.label and self._factkey(a) == self._factkey(b) or (
            a.ctx and a.label.split()[:3] == b.label.split()[:3] and a.ctx == b.ctx)

    def _supersede(self, picked):
        """Drop a row when a higher-authority (or newer-effective) page states the same labelled fact."""
        keep = []
        chs = [self.chunks[p] for p in picked]
        for i, ch in enumerate(chs):
            drop = False
            if ch.kind == "row":
                for j, o in enumerate(chs):
                    if i != j and o.kind == "row" and o.url != ch.url and self._factkey(o) == self._factkey(ch) and ch.label:
                        if o.values != ch.values and (o.auth, o.eff) > (ch.auth, ch.eff):
                            drop = True
            if not drop:
                keep.append(picked[i])
        return keep

    def _extend(self, picked, si):
        top = self.chunks[picked[0]]
        seq = self.seqs.get((top.url, si), [])
        try:
            pos = seq.index(picked[0])
        except ValueError:
            return picked
        out = list(picked)
        total = len(top.display)
        for cid in seq[pos + 1: pos + 5]:
            ch = self.chunks[cid]
            if ch.kind != "text" or total + len(ch.display) > 420 or cid in out:
                break
            out.append(cid)
            total += len(ch.display)
        return out

    def _link_answer(self, question, si):
        uq, res = self.retrieve(question, si, want_link=True)
        if not res:
            return self._refuse()
        best = res[0][1]
        ch = self.chunks[best]
        url = ch.raw
        return Answer(text=f"You can find that here: {url}",
                      sources=[Source(url=url, snapshot_id=self.snaps[si][0], quote=ch.display[:100])])

    # ------------------------------ conflicts
    NUM_COL = re.compile(r"rate|apr|apy|yield|dividend|fee|amount|price|minimum|min\.", re.I)
    SKIP_COL = re.compile(r"payment|points|example|term$|balance|min\. to|minimum to", re.I)

    @staticmethod
    def _num(v):
        m = re.search(r"(\$)?\s*(\d[\d,]*(?:\.\d+)?)\s*(%)?", v or "")
        if not m:
            return None
        return (("$" if m.group(1) else "%" if m.group(3) else ""), round(float(m.group(2).replace(",", "")), 4))

    def conflicts(self):
        if self._conf is not None:
            return self._conf
        self._build()
        last = len(self.snaps) - 1
        obs = defaultdict(list)     # (label, biz, col) -> [(chunk, valstr, num)]
        for ch in self.chunks:
            if ch.kind != "row" or not ch.label or not re.search(r"[a-z]{3}", ch.label) or ch.auth < 0.4:
                continue
            for hh, cc in ch.raw:
                hn = re.sub(r"[^a-z ]", "", (hh or "").lower()).strip()
                if not hn or self.SKIP_COL.search(hn) or not self.NUM_COL.search(hn):
                    continue
                if not re.search(r"\d\s*%|\$", cc or ""):
                    continue
                num = self._num(cc)
                if num is None:
                    continue
                col = "apy" if "yield" in hn or "apy" in hn else ("apr" if "apr" in hn or "annual percentage rate" in hn else "rate")
                obs[(self._fkey(ch)[0], self._fkey(ch)[1], col)].append((ch, cc.strip(), num))
        within, history = [], []
        for key, lst in obs.items():
            nums = {o[2] for o in lst}
            if len(nums) < 2:
                continue
            # live per snapshot
            bysnap = defaultdict(set)
            for ch, v, n in lst:
                for si in ch.snaps:
                    bysnap[si].add(n)
            same_snap = any(len(v) > 1 for v in bysnap.values())
            ever_last = bysnap.get(last, set())
            entry = (key, lst, same_snap)
            (within if same_snap else history).append(entry)
        out = []
        # one conflict per labelled fact: prefer apy, then rate, then apr
        pri = {"apy": 0, "rate": 1, "apr": 2}
        best = {}
        for kind, group in (("within", within), ("history", history)):
            for entry in group:
                k2 = (entry[0][0], entry[0][1])
                cur = best.get(k2)
                rk = (0 if kind == "within" else 1, pri[entry[0][2]], entry[0][2])
                if cur is None or rk < cur[0]:
                    best[k2] = (rk, kind, entry)
        for k2 in sorted(best):
            _, kind, (key, lst, same) = best[k2]
            if True:
                c = self._mk_conflict(key, lst, kind, last)
                if c:
                    out.append(c)
        out.sort(key=lambda x: (x[0], x[1], x[2].topic))
        prose = self._prose_conflicts()
        self._conf = (prose + [c for _, _, c in out])[:80]
        return self._conf

    COUNT_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})*)\+?\s+(?:[a-z]+\s+)?(branch(?:es)?(?: office)?(?: locations?)?|counties)\b", re.I)

    def _prose_conflicts(self):
        last = len(self.snaps) - 1
        by = defaultdict(lambda: defaultdict(list))
        for ch in self.chunks:
            if ch.kind != "text" or last not in ch.snaps or ch.auth < 0.3:
                continue
            for m in self.COUNT_RE.finditer(ch.raw):
                noun = "branch" if m.group(2).lower().startswith("branch") else "counties"
                by[noun][m.group(1).replace(",", "")].append(ch)
        out = []
        for noun, d in sorted(by.items()):
            if len(d) < 2:
                continue
            def score(v):
                chs = d[v]
                return (len({c.url for c in chs}), max(c.auth for c in chs), v)
            win = max(d, key=score)
            vals = [win] + [v for v in sorted(d, key=lambda v: (-len(d[v]), v)) if v != win][:2]
            srcs = []
            for v in vals:
                c0 = sorted(d[v], key=lambda c: (-c.auth, c.url))[0]
                srcs.append(Source(url=c0.url, snapshot_id=self.snaps[last][0], quote=c0.display[:140]))
            out.append(Conflict(topic="number of %s (Dupaco branch count)" % noun, values=vals, sources=srcs, resolution=win,
                                reason="most pages in the newest snapshot state %s; other figures come from older or narrower copy" % win))
        return out

    def _mk_conflict(self, key, lst, kind, last):
        label, biz, col = key
        # winner: observations live in the latest snapshot where the fact appears
        latest_present = max(max(ch.snaps) for ch, _, _ in lst)
        live = [(ch, v, n) for ch, v, n in lst if latest_present in ch.snaps]
        pool = live or lst
        win = max(pool, key=lambda o: (o[0].auth, o[0].eff, max(o[0].snaps), -len(o[1]), o[1]))
        vals = []
        for ch, v, n in sorted(lst, key=lambda o: (-o[0].auth, -max(o[0].snaps), o[1])):
            mm = re.search(r"\$?\s*\d[\d,]*(?:\.\d+)?\s*%?", v)
            vv = re.sub(r"\s+", "", mm.group(0)) if mm else re.sub(r"\s+", "", v)
            if vv not in vals:
                vals.append(vv)
        if len(vals) < 2:
            return None
        vals = vals[:4]
        mm = re.search(r"\$?\s*\d[\d,]*(?:\.\d+)?\s*%?", win[1])
        wv = re.sub(r"\s+", "", mm.group(0)) if mm else re.sub(r"\s+", "", win[1])
        if wv not in vals:
            vals[-1] = wv
        srcs, seen = [], set()
        for ch, v, n in sorted(lst, key=lambda o: (-o[0].auth, -max(o[0].snaps), o[0].url)):
            if (ch.url, v) in seen:
                continue
            seen.add((ch.url, v))
            srcs.append(Source(url=ch.url, snapshot_id=self.snaps[max(ch.snaps)][0], quote=ch.display[:140]))
            if len(srcs) >= 4:
                break
        wch = win[0]
        if kind == "within":
            reason = ("same labelled fact shows different values in snapshot %s; kept the value from the page with the "
                      "highest authority (%s), then newest effective date" % (self.snaps[latest_present][1], self._rank_name(wch.url)))
        else:
            reason = ("value changed between snapshots; kept the newest value from %s in snapshot %s"
                      % (self._rank_name(wch.url), self.snaps[latest_present][1]))
        topic = "%s %s %s%s" % (label, col, "(business) " if biz else "", "")
        topic = re.sub(r"\s+", " ", topic).strip()
        rank = (0 if kind == "within" else 1, -min(o[0].auth for o in lst) * 0 - max(o[0].auth for o in lst))
        return (rank[0], rank[1], Conflict(topic=topic, values=vals, sources=srcs, resolution=wv, reason=reason))

    @staticmethod
    def _rank_name(url):
        a = authority(url)
        return ("official rate sheet" if a >= 1.0 else "product page" if a >= 0.9 else "site page" if a >= 0.6
                else "landing/promo or calculator page" if a >= 0.25 else "low-authority page")


def build():
    return KS()

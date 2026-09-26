#!/usr/bin/env python3
"""Enrich Google Maps school leads with leadership names and public social profile candidates.

This stage deliberately uses public web search results rather than scraping LinkedIn/Facebook/Instagram.
It discovers leadership from the school's own website, then generates/searches public profile candidates
and records evidence + a conservative confidence score.

Optional search provider:
  Set BRAVE_SEARCH_API_KEY to enable Brave Web Search API candidate discovery.
Without a key the script still discovers leadership from school websites and writes ready-to-search queries.
"""

import argparse, csv, html, json, os, re, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (compatible; SchoolLeadershipEnricher/1.0)"
ROLE_PATTERNS = [
    ("Owner", r"\b(owner|proprietor|founder)\b"),
    ("Chairman", r"\b(chairman|chairperson|chairwoman)\b"),
    ("Managing Director", r"\b(managing director|md)\b"),
    ("Director", r"\b(director|school director|academic director)\b"),
    ("Principal", r"\b(principal|head of school|headmistress|headmaster)\b"),
    ("Vice Principal", r"\b(vice[ -]?principal|deputy principal)\b"),
]
LEADERSHIP_HINTS = ("about", "management", "leadership", "principal", "director", "chairman", "team", "message")
SOCIAL_DOMAINS = {
    "linkedin": "linkedin.com/in/",
    "facebook": "facebook.com/",
    "instagram": "instagram.com/",
}

def fetch(url, timeout=6):
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urlopen(req, timeout=timeout) as r:
        return r.read(1_500_000).decode("utf-8", "ignore")

def textify(s):
    s = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def same_host(a, b):
    def host(x): return urlparse(x).netloc.lower().removeprefix("www.")
    return host(a) == host(b)

def leadership_pages(home_url, page):
    links = [home_url]
    for href, label in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page):
        label = textify(label).lower()
        u = urljoin(home_url, href)
        if same_host(home_url, u) and any(h in (u + " " + label).lower() for h in LEADERSHIP_HINTS):
            links.append(u)
    return list(dict.fromkeys(links))[:5]

def extract_people(page_text):
    people = []
    # Capture a nearby name around a leadership title; conservative to avoid arbitrary prose.
    name = r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,4})"
    for role, role_re in ROLE_PATTERNS:
        patterns = [
            rf"{name}\s*[-–—,:|]\s*({role_re})",
            rf"({role_re})\s*[-–—,:|]\s*{name}",
            rf"{name}\s+({role_re})",
        ]
        for p in patterns:
            for m in re.finditer(p, page_text, re.I):
                vals = [g for g in m.groups() if g and re.match(r"^[A-Z]", g)]
                if vals:
                    n = max(vals, key=len).strip(" -–—,:|")
                    if 3 <= len(n) <= 80 and n.lower() not in {"the principal", "our principal"}:
                        people.append((role, n))
    return list(dict.fromkeys(people))

def brave_search(query, count=5):
    key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not key:
        return []
    url = "https://api.search.brave.com/res/v1/web/search?q=" + quote(query) + f"&count={count}"
    req = Request(url, headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": UA})
    with urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    return data.get("web", {}).get("results", [])

def score_candidate(person, school, city, role, result):
    hay = " ".join([result.get("title",""), result.get("description",""), result.get("url","")]).lower()
    score = 0
    if person.lower() in hay: score += 45
    school_words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", school) if w.lower() not in {"school","public","international"}]
    if any(w in hay for w in school_words): score += 25
    if city and city.lower() in hay: score += 10
    if role.lower() in hay: score += 15
    return min(score, 100)

def social_candidates(person, role, school, city):
    out = {}
    for network, domain in SOCIAL_DOMAINS.items():
        q = f'site:{domain} "{person}" "{school}" {city} {role}'
        results = brave_search(q)
        ranked = sorted(((score_candidate(person, school, city, role, r), r) for r in results), reverse=True, key=lambda x:x[0])
        if ranked:
            score, r = ranked[0]
            # Only accept as verified candidate when evidence is reasonably strong.
            if score >= 60:
                out[network] = (r.get("url",""), score, q)
            else:
                out[network] = ("", score, q)
        else:
            out[network] = ("", 0, q)
    return out

def enrich(row):
    website = (row.get("website") or row.get("Website") or "").strip()
    school = (row.get("title") or row.get("name") or row.get("Name") or row.get("school_name") or "").strip()
    city = (row.get("city") or row.get("City") or "").strip()
    if not city:
        address = (row.get("address") or row.get("Address") or "").strip()
        parts = [p.strip() for p in address.split(",") if p.strip()]
        if len(parts) >= 2:
            city = parts[-3] if len(parts) >= 3 else parts[-2]
    found, sources = [], []
    discovered_emails = []
    if website:
        if not website.startswith(("http://","https://")): website = "https://" + website
        try:
            home = fetch(website)
            discovered_emails.extend(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", home, re.I))
            for u in leadership_pages(website, home):
                try:
                    raw_page = home if u == website else fetch(u)
                    discovered_emails.extend(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", raw_page, re.I))
                    txt = textify(raw_page)
                    for role, person in extract_people(txt):
                        found.append((role, person, u))
                    sources.append(u)
                except Exception:
                    pass
        except Exception:
            pass
    if not row.get("emails"):
        emails = []
        for addr in discovered_emails:
            addr = addr.strip(".,;:()[]<>").lower()
            if addr and addr not in emails:
                emails.append(addr)
        row["emails"] = "; ".join(emails[:5])
    # Priority is encoded by ROLE_PATTERNS order.
    rank = {role:i for i,(role,_) in enumerate(ROLE_PATTERNS)}
    found = sorted(list(dict.fromkeys(found)), key=lambda x: rank.get(x[0], 99))
    if not found:
        row.update({
            "leadership_name":"","leadership_role":"","leadership_source":"",
            "linkedin_person":"","facebook_person":"","instagram_person":"",
            "profile_confidence":0,"verification_status":"not_found",
            "linkedin_search_query":f'"{school}" principal LinkedIn {city}',
            "facebook_search_query":f'"{school}" principal Facebook {city}',
            "instagram_search_query":f'"{school}" principal Instagram {city}',
        })
        return row
    role, person, source = found[0]
    socials = social_candidates(person, role, school, city)
    scores = [v[1] for v in socials.values()]
    best = max(scores or [0])
    row.update({
        "leadership_name":person, "leadership_role":role, "leadership_source":source,
        "linkedin_person":socials["linkedin"][0],
        "facebook_person":socials["facebook"][0],
        "instagram_person":socials["instagram"][0],
        "profile_confidence":best,
        "verification_status":"verified_candidate" if best >= 75 else ("candidate" if best >= 60 else "leadership_only"),
        "linkedin_search_query":socials["linkedin"][2],
        "facebook_search_query":socials["facebook"][2],
        "instagram_search_query":socials["instagram"][2],
    })
    return row

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("-o","--output", default="school_leadership_enriched.csv")
    ap.add_argument("--delay", type=float, default=0.0, help="Optional delay between completed rows")
    ap.add_argument("--workers", type=int, default=8, help="Parallel website enrichment workers")
    ap.add_argument("--skip-email", action="store_true", help="Do not add emails discovered on school websites")
    args = ap.parse_args()
    with open(args.input_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    enriched = []
    def work(row):
        out = enrich(dict(row))
        if args.skip_email:
            out["emails"] = ""
        return out
    workers = max(1, min(16, args.workers))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, out in enumerate(ex.map(work, rows), 1):
            print(f"[{i}/{len(rows)}] enriched {out.get('title') or out.get('name') or out.get('Name') or ''}", flush=True)
            enriched.append(out)
            if args.delay:
                time.sleep(args.delay)
    fields = list(dict.fromkeys([k for r in enriched for k in r.keys()]))
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(enriched)
    print(f"Wrote {len(enriched)} rows to {args.output}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch new physiotherapy papers from PubMed and append them to data/entries.json.

Env vars (all optional):
  GEMINI_API_KEY     -> free option: Google Gemini writes English + Hindi summaries
  ANTHROPIC_API_KEY  -> paid option: Claude writes the summaries (used only if no Gemini key)
  GEMINI_MODEL       -> default gemini-2.5-flash (check the current name in Google AI Studio)
  ANTHROPIC_MODEL    -> default claude-sonnet-5-5
  ENTRIES_PER_DAY    -> default 3
  NCBI_API_KEY       -> optional, raises PubMed rate limit
With no AI key the page still works: it shows the abstract's conclusion in English only.
"""
import datetime as dt, json, os, re, sys, time
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "entries.json")
PER_DAY = int(os.getenv("ENTRIES_PER_DAY", "3"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

QUERY = (
    '("Physical Therapy Modalities"[MeSH] OR physiotherap*[tiab] OR "physical therapy"[tiab] '
    'OR "exercise therapy"[tiab] OR rehabilitation[ti]) '
    'AND (randomized controlled trial[pt] OR systematic review[pt] OR meta-analysis[pt]) '
    'AND hasabstract AND english[lang]'
)

# Specialization labels shown on the page. First match wins, so order matters.
SPECIALIZATIONS = [
    ("Sports", ["athlete", "sport", "anterior cruciate", " acl ", "hamstring", "runner", "return to play"]),
    ("Neurological", ["stroke", "parkinson", "spinal cord", "multiple sclerosis", "cerebral palsy", "neuro",
                      "hemipleg", "traumatic brain", "vestibular"]),
    ("Cardiopulmonary", ["cardiac", "pulmonary", "copd", "respiratory", "heart failure", "cardiopulmonary",
                         "lung", "asthma", "covid"]),
    ("Paediatric", ["child", "pediatric", "paediatric", "infant", "adolescent", "neonat"]),
    ("Women's health", ["pelvic floor", "pregnan", "postpartum", "incontinence", "menopaus", "breast cancer"]),
    ("Geriatric", ["older adults", "elderly", "falls", "frailty", "sarcopenia", "dementia", "aged "]),
    ("Orthopaedic", ["knee", "shoulder", "back pain", "neck pain", "osteoarthritis", "tendin", "fracture",
                     " hip ", "arthroplasty", "musculoskeletal", "lumbar", "ankle", "spine", "spinal",
                     "rotator cuff", "plantar", "temporomandibular", "carpal tunnel"]),
    ("Pain", [" pain"]),
]

def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()

def eutils(endpoint, **params):
    if os.getenv("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    time.sleep(0.4)  # stay under PubMed's rate limit
    return http(EUTILS + endpoint + "?" + urllib.parse.urlencode(params))

def load():
    try:
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def search_ids():
    raw = eutils("esearch.fcgi", db="pubmed", term=QUERY, retmode="json", retmax=80,
                 sort="date", datetype="edat", reldate=21)
    return json.loads(raw)["esearchresult"]["idlist"]

def text(el):
    return "".join(el.itertext()).strip() if el is not None else ""

def parse_article(art):
    pmid = text(art.find(".//MedlineCitation/PMID"))
    title = text(art.find(".//ArticleTitle")).rstrip(".")
    parts = []
    for ab in art.findall(".//Abstract/AbstractText"):
        label = ab.get("Label")
        parts.append((label + ": " if label else "") + text(ab))
    abstract = "\n".join(p for p in parts if p.strip())
    if not (pmid and title and abstract):
        return None
    ptypes = [text(p) for p in art.findall(".//PublicationType")]
    stype = next((n for k, n in [("Meta-Analysis", "Meta-analysis"), ("Systematic Review", "Systematic review"),
                                 ("Randomized Controlled Trial", "Randomized trial")] if k in ptypes), "")
    year = text(art.find(".//JournalIssue/PubDate/Year")) or text(art.find(".//ArticleDate/Year"))
    if not year:
        m = re.search(r"\d{4}", text(art.find(".//JournalIssue/PubDate/MedlineDate")))
        year = m.group(0) if m else ""
    hay = " " + (title + " " + abstract).lower() + " "
    spec = next((name for name, kws in SPECIALIZATIONS if any(k in hay for k in kws)), "General")
    return {"pmid": pmid, "title": title, "abstract": abstract, "study_type": stype, "topic": spec,
            "journal": text(art.find(".//Journal/Title")), "year": year,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"}

def fetch_candidates(ids):
    if not ids:
        return []
    root = ET.fromstring(eutils("efetch.fcgi", db="pubmed", id=",".join(ids), retmode="xml"))
    by_id = {}
    for art in root.findall(".//PubmedArticle"):
        p = parse_article(art)
        if p:
            by_id[p["pmid"]] = p
    return [by_id[i] for i in ids if i in by_id]  # keep newest-first order

def choose(cands, n):
    """Prefer a mix of specializations; fill any remaining slots with the newest papers."""
    picked, used = [], set()
    for p in cands:
        if len(picked) < n and p["topic"] not in used:
            picked.append(p); used.add(p["topic"])
    for p in cands:
        if len(picked) < n and p not in picked:
            picked.append(p)
    return picked

def fallback_summary(abstract):
    concl = re.search(r"(?:CONCLUSIONS?|RESULTS?)\s*:\s*(.+)", abstract, re.S | re.I)
    body = (concl.group(1) if concl else abstract).replace("\n", " ")
    sents = re.split(r"(?<=[.!?])\s+", body)
    return {"summary": {"en": " ".join(sents[:3])}, "takeaway": {}}

def ask_llm(prompt):
    """Returns raw model text, or None if no key is configured."""
    gkey, akey = os.getenv("GEMINI_API_KEY"), os.getenv("ANTHROPIC_API_KEY")
    if gkey:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}}
        raw = http(url, json.dumps(body).encode(), {"content-type": "application/json", "x-goog-api-key": gkey})
        time.sleep(6)  # be gentle with free-tier per-minute limits
        return json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"]
    if akey:
        body = json.dumps({"model": ANTHROPIC_MODEL, "max_tokens": 1500,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        raw = http("https://api.anthropic.com/v1/messages", body,
                   {"content-type": "application/json", "x-api-key": akey, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in json.loads(raw)["content"])
    return None

def summarize(p):
    prompt = (
        "You write a daily physiotherapy research digest for students and clinicians. Using ONLY the abstract below, "
        "return JSON with keys summary_en, takeaway_en, summary_hi, takeaway_hi. "
        "summary: 3-4 plain-language sentences covering who was studied, what was done and what was found (include key numbers if given). "
        "takeaway: one sentence on what this could mean for practice, with honest caution if the evidence is limited. "
        "Hindi must be simple Hindi in Devanagari; keep standard clinical terms in English where natural. "
        "Do not invent facts. Return only JSON.\n\n"
        f"Title: {p['title']}\nStudy type: {p['study_type']}\nAbstract:\n{p['abstract']}"
    )
    try:
        txt = ask_llm(prompt)
        if txt is None:
            return fallback_summary(p["abstract"])
        d = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        return {"summary": {"en": d["summary_en"], "hi": d.get("summary_hi", "")},
                "takeaway": {"en": d.get("takeaway_en", ""), "hi": d.get("takeaway_hi", "")}}
    except Exception as e:
        print("Summary failed, using fallback:", e, file=sys.stderr)
        return fallback_summary(p["abstract"])

def main():
    entries = load()
    seen = {e["pmid"] for e in entries}
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    have = sum(1 for e in entries if e["date"] == today)
    need = PER_DAY - have
    if need <= 0:
        print("Already have today's entries."); return
    ids = [i for i in search_ids() if i not in seen][:40]
    for p in choose(fetch_candidates(ids), need):
        s = summarize(p)
        entries.append({"date": today, "pmid": p["pmid"], "title": p["title"], "study_type": p["study_type"],
                        "topic": p["topic"], "journal": p["journal"], "year": p["year"], "url": p["url"],
                        "summary": s["summary"], "takeaway": s["takeaway"]})
        print(f"Added [{p['topic']}]: {p['title']}")
    entries.sort(key=lambda e: e["date"], reverse=True)
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()

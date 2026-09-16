#!/usr/bin/env python3
"""The client etat de situation.

Berthier's staff system worked because every corps reported in an IDENTICAL
form, so reading the 3rd cost exactly what reading the 1st cost. The same
CLAUDE.md rule already governs the Claude lanes. This applies it to clients.

One line each, always these fields, always this order:

  CLIENT | last <n>d | in <n> | out <n> | owes | waiting <on whom> | next

Nothing here is invented. Every column comes out of the Hermes database that is
already running, so the board cannot claim a conversation that did not happen.
"""
import re, sqlite3, sys
try:
    import tomllib                      # 3.11+
except ModuleNotFoundError:             # ghengis runs an older default python
    tomllib = None
from datetime import datetime, timezone
from pathlib import Path

DB = Path.home() / ".hermes" / "state.sqlite"
CFG = Path.home() / "Projects" / "hermes" / "clients.toml"
NOW = datetime.now(timezone.utc)

# Senders that the classifier files as "client" but which are not clients:
# our own products, carriers, registrars, and personal mail. Without this the
# board's biggest "client" is bullpenlm.com, which is us.
NOT_CLIENTS = re.compile(
    # our own products
    r"(bullpenlm|beers\.cash|aurajam|lootlens|deedstash|killsesh|scopeweb|rollick"
    r"|dittomethis|ranger-beers|charliecareapp|insomnimania|gormers"
    # insurance: carriers, IMOs, contracting. Dylan's licence, not his clients.
    r"|pennmutual|transamerica|americo|corebridge|foresters|surelc|integrity"
    r"|ascent|familyfirstlife|supremelifebrokerage|uigus|calsurance|mutualofomaha"
    r"|ethos-agents|ffl"
    # vendors and platforms we pay, which are the opposite of clients
    r"|stripe|venmo|vercel|opensrs|telnyx|airbnb|opentable|usmobile|bmo|aexp"
    r"|x\.com|google|apple|github|cloudflare|namecheap|godaddy"
    # personal, government, education
    r"|army\.mil|\.gov$|\.edu$|oregon\.gov|modernstates"
    # generic mail hosts carry no client identity at all
    r"|^gmail\.|^mail\.|^e\.|^email\.|^support\.|^no-?reply)", re.I)

def days(ts):
    if not ts: return None
    try: d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError: return None
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return (NOW - d).days

def _mini_toml(text):
    """Enough TOML for clients.toml: [section] headers plus key = value.

    Shipping a dependency to read four fields off a config file is worse than
    thirty lines that only understand the four fields."""
    out, cur = {}, None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line: continue
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1].strip("[]"); out.setdefault(cur, {}); continue
        if cur is None or "=" not in line: continue
        k, v = (x.strip() for x in line.split("=", 1))
        if v.startswith('"') and v.endswith('"'): v = v[1:-1]
        elif v in ("true", "false"): v = v == "true"
        else:
            try: v = int(v)
            except ValueError: continue
        out[cur][k] = v
    return out

def load_clients():
    if not CFG.exists(): return {}
    data = tomllib.loads(CFG.read_text()) if tomllib else _mini_toml(CFG.read_text())
    out = {}
    for name, c in data.items():
        if not isinstance(c, dict): continue
        if name in ("partners", "leads"): continue      # not clients, config groups
        if "." in name: continue                        # [boarhog.payment_methods] etc
        if "domain" not in c and "contact_email" not in c and not c.get("billing_amount_cents"):
            # a bare [section] with nothing in it. Keep it, and say so.
            out[name] = {"domain": "", "email": "", "monthly": 0, "active": True,
                         "stub": True}
            continue
        out[name] = {"domain": c.get("domain", ""),
                     "email": c.get("contact_email", ""),
                     "monthly": c.get("billing_amount_cents", 0) / 100,
                     "active": c.get("active", True), "stub": False}
    return out

def main():
    if not DB.exists(): sys.exit(f"no database at {DB}")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = db.execute

    known = load_clients()

    # every domain the classifier has ever called a client, minus the noise
    rows = q("""SELECT lower(substr(sender, instr(sender,'@')+1)) dom,
                       COUNT(*) n, MAX(received_at) last
                FROM messages WHERE classification='client'
                GROUP BY dom""").fetchall()
    doms = {}
    for dom, n, last in rows:
        dom = re.split(r"[^a-z0-9.-]", dom.strip(" >"))[0].strip(".")
        if not dom or NOT_CLIENTS.search(dom): continue
        d = doms.setdefault(dom, {"n": 0, "last": None})
        d["n"] += n
        if not d["last"] or last > d["last"]: d["last"] = last

    # what the classifier filed under each client's own label
    bylabel = {}
    for lbl, n, last in q("""SELECT label, COUNT(*), MAX(received_at) FROM messages
                             WHERE label LIKE 'Hermes/Client/%' GROUP BY label"""):
        bylabel[lbl.rsplit("/", 1)[-1].lower()] = {"n": n, "last": last}

    # map configured clients onto their domain rows AND their label rows
    board, claimed = [], set()
    for name, c in sorted(known.items()):
        dom = c["domain"].lower()
        hit = dict(doms.get(dom, {"n": 0, "last": None}))
        lab = bylabel.get(name.lower())
        if lab:
            hit["n"] = max(hit["n"], lab["n"])
            if not hit["last"] or (lab["last"] and lab["last"] > hit["last"]):
                hit["last"] = lab["last"]
        claimed.add(dom)
        board.append((name.upper(), dom, hit["n"], hit["last"], c["monthly"], True, c.get("stub", False)))
    for dom, d in doms.items():
        if dom in claimed: continue
        board.append((dom.split(".")[0].upper(), dom, d["n"], d["last"], 0.0, False, False))

    # A board nobody can read at a glance is not a board. The roster is the
    # report; everything else is a short list of candidates to fold in or
    # rule out, capped so it can never swamp the thing you came to read.
    roster = sorted([b for b in board if b[5]], key=lambda r: -(days(r[3]) or 0))
    cands  = sorted([b for b in board if not b[5] and b[2] >= 6],
                    key=lambda r: -r[2])[:6]

    print(f"\n  CLIENT BOARD   {NOW:%a %d %b %Y  %H:%M} UTC\n")
    print(f"  {'CLIENT':<14} {'LAST':>6}  {'MAIL':>5}  {'MONTHLY':>8}  {'STATE':<7} NEXT")
    print("  " + "-" * 84)
    owed = 0.0
    for name, dom, n, last, monthly, _, stub in roster:
        d = days(last)
        age = f"{d}d" if d is not None else "never"
        fee = f"${monthly:,.0f}" if monthly else "-"
        if stub:        state, nxt = "STUB",   "empty section in clients.toml. No domain, no billing"
        elif d is None: state, nxt = "SILENT", "no mail ever through this inbox"
        elif d > 60:    state, nxt = "COLD",   f"{d} days. Re-open it or close the file"
        elif d > 21:    state, nxt = "QUIET",  f"{d} days silent. Send a status note"
        else:           state, nxt = "WARM",   "in conversation"
        if monthly and d is not None and d > 30: owed += monthly
        print(f"  {name:<14} {age:>6}  {n:>5}  {fee:>8}  {state:<7} {nxt}")

    if cands:
        print(f"\n  NOT ON THE ROSTER, but talking to us")
        print("  " + "-" * 84)
        for name, dom, n, last, _, _, _ in cands:
            d = days(last)
            print(f"  {dom:<28} {n:>4} msgs   last {d}d ago   -> client, or rule it out?")

    led = q("SELECT direction, COUNT(*), COALESCE(SUM(amount_cents),0)/100.0 FROM ledger GROUP BY direction").fetchall()
    tcol = next((c for c in ("detected_at","created_at","received_at")
                 if _has(db, "leads", c)), None)
    leads = q(f"SELECT COUNT(*), MAX(date({tcol})) FROM leads").fetchone() if tcol \
            else (q("SELECT COUNT(*) FROM leads").fetchone()[0], "unknown")
    print("\n  " + "-" * 86)
    for direction, n, usd in led:
        word = "received" if direction == "in" else "billed out"
        print(f"  ledger {word:<11} {n:>4} rows   ${usd:,.0f}")
    print(f"  leads captured    {leads[0]:>4}        last {leads[1] or 'never'}")
    if owed: print(f"  UNBILLED RISK             ${owed:,.0f}  monthly fees on clients silent 30+ days")
    print()

def _has(db, table, col):
    try:
        return any(r[1] == col for r in db.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False

if __name__ == "__main__":
    main()

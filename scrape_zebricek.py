# scrape_zebricek.py
# CSV hlavička:
# Poradi;Příjmení a jméno;Rok.nar.;Oddil;Body;Svaz;Kategorie;Rocnik

import os, csv, time, pathlib, re
from playwright.sync_api import sync_playwright

BASE    = "https://stis.ping-pong.cz"
OUTDIR  = "data"
DEBUG   = os.path.join(OUTDIR, "debug")
SVAZY   = os.getenv("ZEBR_SVAZY", "420210").split(",")   # default: Praha-západ
ROCNIK  = os.getenv("ROCNIK", "2025")
KAT     = os.getenv("KATEGORIE", "s")                    # např. "s" (dospělí)
ZVYSS   = os.getenv("ZVYSSICH", "ano")                   # "ano" / "ne"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

pathlib.Path(OUTDIR).mkdir(parents=True, exist_ok=True)
pathlib.Path(DEBUG).mkdir(parents=True, exist_ok=True)

# ---------- helpers ----------
def cells_texts(row):
    cells = row.query_selector_all("td, th")
    out = []
    for c in cells:
        try:
            out.append((c.inner_text() or "").strip())
        except:
            out.append("")
    return out

def is_year(s: str) -> bool:
    return bool(re.fullmatch(r"(19|20)\d{2}", s.strip()))

def is_number(s: str) -> bool:
    t = s.strip().replace(",", ".")
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", t))

def to_number(s: str) -> float:
    try:
        return float(s.replace(",", "."))
    except:
        return 0.0

def find_table(page):
    # zkusí několik variant; když nic, vezme první <table> na stránce
    sels = [
        "table.zebricek, table.zebricekstr, table.table-bordered.zebricek",
        "table.table-striped.table-bordered", "table.table-bordered", "table"
    ]
    for sel in sels:
        el = page.query_selector(sel)
        if el:
            return el
    return None

def warmup(page):
    page.set_extra_http_headers({"Accept-Language":"cs,en;q=0.8"})
    page.set_default_timeout(45000)
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("load", timeout=5000)
    except:
        pass
    page.wait_for_timeout(400)

def parse_table(tbl, svaz, kat, rocnik):
    rows = tbl.query_selector_all("tr")
    out = []
    # pokus o hlavičku (ignoruj řádky, kde 1. buňka není pořadí)
    for r in rows:
        cols = cells_texts(r)
        if not cols:
            continue
        first = cols[0].strip()
        if not first or not first.split()[0].isdigit():
            continue
        # poradi
        m = re.match(r"^\d+", first)
        if not m: 
            continue
        poradi = m.group(0)

        # year / name / oddil / body – heuristika
        year_idx = next((i for i, v in enumerate(cols) if is_year(v)), -1)
        roc = cols[year_idx].strip() if year_idx >= 0 else ""

        # body = poslední číselná buňka (kromě poradi)
        body_idx = -1
        for i in range(len(cols)-1, -1, -1):
            if i == 0: 
                continue
            if is_number(cols[i]):
                body_idx = i
                break
        body = cols[body_idx].strip() if body_idx >= 0 else ""

        # oddil = poslední „textová“ buňka před body
        oddil = ""
        if body_idx > 0:
            for i in range(body_idx-1, 0, -1):
                v = cols[i].strip()
                if v and not is_number(v) and not is_year(v):
                    oddil = v
                    break

        # jméno = co nejpravděpodobnější text uprostřed (nejdelší text mimo oddil/body/poradi)
        name = ""
        best_len = 0
        for i, v in enumerate(cols):
            if i in (0, year_idx, body_idx):
                continue
            if v and v != oddil and not is_number(v):
                L = len(v)
                if L > best_len:
                    best_len = L
                    name = v

        out.append([poradi, name, roc, oddil, body, svaz, kat.upper(), rocnik])
    return out

def export_zebricek(page, svaz, rocnik, kat, zvyss):
    url = f"{BASE}/zebricekstr-oblast/svaz-{svaz}/rocnik-{rocnik}/kategorie-{kat}/zvyssich-{zvyss}"
    attempts = 8
    for a in range(1, attempts+1):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("load", timeout=8000)
        except:
            pass
        page.wait_for_timeout(800 + 200*a)

        tbl = find_table(page)
        if tbl:
            rows = parse_table(tbl, svaz, kat, rocnik)
            if rows:
                outp = os.path.join(OUTDIR, f"zebricek_{svaz}_{rocnik}_kat-{kat}.csv")
                with open(outp, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["Poradi","Příjmení a jméno","Rok.nar.","Oddil","Body","Svaz","Kategorie","Rocnik"])
                    w.writerows(rows)
                print(f"{svaz}: žebříček ({kat}) {len(rows)} řádků -> {outp}")
                return

        # debug + retry
        html = page.content()
        with open(os.path.join(DEBUG, f"zebricek_{svaz}_attempt{a}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        try:
            page.screenshot(path=os.path.join(DEBUG, f"zebricek_{svaz}_attempt{a}.png"), full_page=True)
        except:
            pass
        time.sleep(0.6 + 0.4*a)

    raise RuntimeError(f"Žebříček pro svaz {svaz} se nepodařilo načíst.")

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage","--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, viewport={"width":1366,"height":900}, locale="cs-CZ")
        page = ctx.new_page()
        warmup(page)
        for svaz in [s.strip() for s in SVAZY if s.strip()]:
            export_zebricek(page, svaz, ROCNIK, KAT, ZVYSS)
        browser.close()

if __name__ == "__main__":
    main()

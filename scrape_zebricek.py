# scrape_zebricek.py
# CSV: Poradi;Příjmení a jméno;Rok.nar.;Oddil;Body;Svaz;Kategorie;Rocnik

import os, csv, time, pathlib, re, unicodedata
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE    = "https://stis.ping-pong.cz"
OUTDIR  = "data"
DEBUG   = os.path.join(OUTDIR, "debug")
SVAZY   = [s.strip() for s in os.getenv("ZEBR_SVAZY", "420210").split(",") if s.strip()]
ROCNIK  = os.getenv("ROCNIK", "2025")
KAT     = os.getenv("KATEGORIE", "s")             # "s" = dospělí
ZVYSS   = os.getenv("ZVYSSICH", "ano")            # "ano" / "ne"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

pathlib.Path(OUTDIR).mkdir(parents=True, exist_ok=True)
pathlib.Path(DEBUG).mkdir(parents=True, exist_ok=True)

def stripdia(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")

def normhdr(s: str) -> str:
    s = stripdia(s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s

def cells_texts(row):
    cells = row.query_selector_all("th, td")
    out = []
    for c in cells:
        try:
            out.append((c.inner_text() or "").strip())
        except:
            out.append("")
    return out

def warmup(page):
    page.set_extra_http_headers({"Accept-Language":"cs,en;q=0.8"})
    page.set_default_timeout(45000)
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("load", timeout=5000)
    except:
        pass
    page.wait_for_timeout(300)

def wait_rows_ready(page, timeout_ms=60000):
    # počkej, až bude na stránce aspoň jedna tabulka s > 5 řádky v <tbody>
    page.wait_for_function(
        """() => {
            const tbls = Array.from(document.querySelectorAll('table'));
            for (const t of tbls) {
              const tb = t.tBodies && t.tBodies[0];
              if (!tb) continue;
              const rows = tb.querySelectorAll('tr');
              if (rows.length >= 5) return true;
            }
            return false;
        }""",
        timeout=timeout_ms
    )

def find_best_table(page):
    tbls = page.query_selector_all("table")
    best = None
    best_score = -1
    for t in tbls:
        score = 0
        # skóruj podle hlaviček
        thead = t.query_selector("thead")
        headers = []
        if thead:
            hrow = thead.query_selector("tr")
            if hrow:
                headers = [normhdr(x) for x in cells_texts(hrow)]
        else:
            # fallback: vezmi první řádek jako pseudo hlavičku
            tr0 = t.query_selector("tr")
            if tr0:
                headers = [normhdr(x) for x in cells_texts(tr0)]
        for h in headers:
            if "por" in h: score += 1       # pořadí
            if "jmen" in h: score += 2      # jméno
            if "rok" in h and ("nar" in h or "naro" in h): score += 2
            if "oddil" in h or "klub" in h or "tym" in h: score += 2
            if "body" in h: score += 3
        # přidej body za počet řádků
        tb = t.query_selector("tbody") or t
        rcnt = len(tb.query_selector_all("tr"))
        score += min(rcnt, 100) / 20.0
        if score > best_score:
            best = t; best_score = score
    return best

def map_columns(table):
    # vrať indexy sloupců podle textu hlavičky
    thead = table.query_selector("thead")
    hdrs = []
    if thead:
        tr = thead.query_selector("tr")
        if tr:
            hdrs = [normhdr(x) for x in cells_texts(tr)]
    # mapování
    idx = {"poradi": None, "jmeno": None, "rok": None, "oddil": None, "body": None}
    for i, h in enumerate(hdrs):
        if idx["poradi"] is None and ("poradi" in h or "por ad" in h or "po" in h and "adi" in h):
            idx["poradi"] = i
        if idx["jmeno"] is None and ("jmen" in h or "prijmeni" in h):
            idx["jmeno"] = i
        if idx["rok"] is None and ("rok" in h and ("nar" in h or "naro" in h)):
            idx["rok"] = i
        if idx["oddil"] is None and ("oddil" in h or "klub" in h or "tym" in h):
            idx["oddil"] = i
        if idx["body"] is None and ("body" in h):
            idx["body"] = i
    return idx

def is_year(s: str) -> bool:
    return bool(re.fullmatch(r"(19|20)\d{2}", s.strip()))

def is_num(s: str) -> bool:
    t = s.strip().replace(",", ".")
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", t))

def parse_page_rows(table, svaz):
    # Přečti všechny řádky aktuální stránky tabulky
    out = []
    tbody = table.query_selector("tbody") or table
    rows = tbody.query_selector_all("tr")
    hdrmap = map_columns(table)
    for r in rows:
        cols = cells_texts(r)
        if not cols: continue
        # vynech řádky bez čísla pořadí
        if not any(ch.isdigit() for ch in cols[0]):
            # fallback: některé tabulky mají v prvním sloupci prázdno a pořadí jinde
            pass
        poradi = ""
        jmeno  = ""
        rok    = ""
        oddil  = ""
        body   = ""

        # preferuj mapování z hlavičky
        def get(i):
            return cols[i].strip() if (i is not None and i < len(cols)) else ""
        poradi = get(hdrmap["poradi"])
        jmeno  = get(hdrmap["jmeno"])
        rok    = get(hdrmap["rok"])
        oddil  = get(hdrmap["oddil"])
        body   = get(hdrmap["body"])

        # doplň fallbacky heuristikou
        if not poradi:
            m = re.match(r"^\d+", cols[0].strip())
            poradi = m.group(0) if m else ""
        if not rok:
            for v in cols:
                if is_year(v): rok = v; break
        if not body:
            # poslední číselná buňka (kromě poradi)
            for v in reversed(cols[1:]):
                if is_num(v): body = v; break
        if not oddil:
            # poslední „textová“ buňka před body
            if body:
                bi = next((i for i in range(len(cols)-1, -1, -1) if cols[i].strip() == body), -1)
            else:
                bi = len(cols)
            for i in range(bi-1, 0, -1):
                v = cols[i].strip()
                if v and not is_num(v) and not is_year(v):
                    oddil = v; break
        if not jmeno:
            # nejdelší text mimo oddíl/čísla/rok
            best = ""
            for v in cols[1:]:
                if v and v != oddil and not is_num(v) and not is_year(v):
                    if len(v) > len(best): best = v
            jmeno = best

        if not poradi and not jmeno:
            continue

        out.append([poradi, jmeno, rok, oddil, body, svaz, KAT.upper(), ROCNIK])
    return out

def click_next_if_any(page, table):
    # zkus různé podoby "Next" v DataTables
    candidates = [
        "a.paginate_button.next:not(.disabled)",
        "li.paginate_button.next:not(.disabled) a",
        "a[aria-label='Next']:not(.disabled)",
        "button[aria-label='Next']:not([disabled])",
    ]
    for sel in candidates:
        el = page.query_selector(sel)
        if el:
            # porovnáme první řádek před/po kliku, abychom poznali změnu stránky
            tb = table.query_selector("tbody") or table
            first_before = (tb.query_selector("tr td") or tb.query_selector("tr th"))
            before_txt = first_before.inner_text().strip() if first_before else ""
            el.click()
            try:
                page.wait_for_timeout(400)
                page.wait_for_function(
                    """(txt) => {
                        const tb = document.querySelector('table tbody') || document.querySelector('table');
                        if (!tb) return false;
                        const cell = tb.querySelector('tr td, tr th');
                        const now = cell ? (cell.textContent||'').trim() : '';
                        return now && now !== txt;
                    }""",
                    timeout=4000,
                    arg=before_txt
                )
            except PWTimeout:
                # možná žádná další stránka
                return False
            return True
    return False

def export_zebricek(page, svaz):
    url = f"{BASE}/zebricekstr-oblast/svaz-{svaz}/rocnik-{ROCNIK}/kategorie-{KAT}/zvyssich-{ZVYSS}"
    attempts = 6
    for a in range(1, attempts+1):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else None
        # krátce počkej na řádky
        try:
            wait_rows_ready(page, timeout_ms=20000 + a*5000)
        except PWTimeout:
            pass

        table = find_best_table(page)
        rows_all = []
        if table:
            # seber řádky z aktuální stránky
            rows_all.extend(parse_page_rows(table, svaz))
            # projdi případné stránkování
            pagesteps = 0
            while click_next_if_any(page, table) and pagesteps < 50:
                table = find_best_table(page) or table
                rows_all.extend(parse_page_rows(table, svaz))
                pagesteps += 1

        if rows_all:
            outp = os.path.join(OUTDIR, f"zebricek_{svaz}_{ROCNIK}_kat-{KAT}.csv")
            with open(outp, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Poradi","Příjmení a jméno","Rok.nar.","Oddil","Body","Svaz","Kategorie","Rocnik"])
                w.writerows(rows_all)
            print(f"{svaz}: žebříček ({KAT}) {len(rows_all)} řádků -> {outp}")
            return

        # --- debug + další pokus ---
        html = page.content()
        with open(os.path.join(DEBUG, f"zebricek_{svaz}_attempt{a}_status{status or 0}.html"), "w", encoding="utf-8") as f:
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
        for svaz in SVAZY:
            export_zebricek(page, svaz)
        browser.close()

if __name__ == "__main__":
    main()

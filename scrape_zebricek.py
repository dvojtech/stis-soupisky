# scrape_zebricek.py
# CSV: Poradi;Příjmení a jméno;Rok.nar.;Oddil;Zápasy;STR;STR stabil;STR+-;Svaz;Kategorie;Rocnik

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

# ---------- util ----------
def stripdia(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")

def normhdr(s: str) -> str:
    t = stripdia(s).lower()
    t = t.replace("•", " ").replace("±", "+-").replace("+−", "+-").replace("–", "-").replace("—", "-")
    t = re.sub(r"[^a-z0-9+\- ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

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
    page.wait_for_function(
        """() => {
            const tbls = Array.from(document.querySelectorAll('table'));
            for (const t of tbls) {
              const tb = t.tBodies && t.tBodies[0];
              if (!tb) continue;
              if (tb.querySelectorAll('tr').length >= 3) return true;
            }
            return false;
        }""",
        timeout=timeout_ms
    )

def find_best_table(page):
    tbls = page.query_selector_all("table")
    best, score_best = None, -1
    for t in tbls:
        sc = 0
        thead = t.query_selector("thead")
        headers = []
        if thead:
            tr = thead.query_selector("tr")
            if tr: headers = [normhdr(x) for x in cells_texts(tr)]
        else:
            tr0 = t.query_selector("tr")
            if tr0: headers = [normhdr(x) for x in cells_texts(tr0)]

        for h in headers:
            if "por" in h: sc += 1
            if "hrac" in h or "jmen" in h or "prijmeni" in h: sc += 2
            if "rok" in h and ("nar" in h or "naro" in h): sc += 2
            if "oddil" in h or "klub" in h or "tym" in h: sc += 2
            if "zapasy" in h: sc += 3
            if h == "str": sc += 3
            if "str stabil" in h: sc += 3
            if "str+-" in h: sc += 3
        tb = t.query_selector("tbody") or t
        sc += min(len(tb.query_selector_all("tr")), 100) / 20.0
        if sc > score_best:
            best, score_best = t, sc
    return best

def map_columns(table):
    thead = table.query_selector("thead")
    hdrs = []
    if thead:
        tr = thead.query_selector("tr")
        if tr: hdrs = [normhdr(x) for x in cells_texts(tr)]

    idx = {"poradi": None, "jmeno": None, "rok": None, "oddil": None,
           "zapasy": None, "str": None, "str_stabil": None, "str_pm": None}

    for i, h in enumerate(hdrs):
        # pořadí (někdy nijak nepojmenované – necháme fallback)
        if idx["poradi"] is None and ("poradi" in h or h in ("por", "#")):
            idx["poradi"] = i

        # jméno
        if idx["jmeno"] is None and ("hrac" in h or "jmen" in h or "prijmeni" in h):
            idx["jmeno"] = i

        # rok narození
        if idx["rok"] is None and ("rok" in h and ("nar" in h or "naro" in h)):
            idx["rok"] = i

        # oddíl
        if idx["oddil"] is None and ("oddil" in h or "klub" in h or "tym" in h):
            idx["oddil"] = i

        # zápasy
        if idx["zapasy"] is None and "zapasy" in h:
            idx["zapasy"] = i

        # důležité: nejdřív STR stabil / STR+-, až potom prostý STR
        if idx["str_stabil"] is None and ("str stabil" in h or h == "strstabil"):
            idx["str_stabil"] = i
        if idx["str_pm"] is None and ("str+-" in h or "str +-" in h):
            idx["str_pm"] = i
        if idx["str"] is None and h == "str":
            idx["str"] = i

    return idx

def is_year(s: str) -> bool:
    return bool(re.fullmatch(r"(19|20)\d{2}", s.strip()))

def is_num(s: str) -> bool:
    t = s.strip().replace(",", ".")
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", t))

def parse_page_rows(table, svaz):
    out = []
    tbody = table.query_selector("tbody") or table
    rows = tbody.query_selector_all("tr")
    hdrmap = map_columns(table)

    for r in rows:
        cols = cells_texts(r)
        if not cols: 
            continue

        # --- fields ---
        def get(i):
            return cols[i].strip() if (i is not None and i < len(cols)) else ""

        poradi = get(hdrmap["poradi"])
        jmeno  = get(hdrmap["jmeno"])
        rok    = get(hdrmap["rok"])
        oddil  = get(hdrmap["oddil"])
        zapasy = get(hdrmap["zapasy"])
        str_v  = get(hdrmap["str"])
        str_s  = get(hdrmap["str_stabil"])
        str_pm = get(hdrmap["str_pm"])

        # --- fallbacky ---
        if not poradi:
            m = re.match(r"^\s*(\d+)", cols[0])
            poradi = m.group(1) if m else ""
        if not rok:
            for v in cols:
                if is_year(v): rok = v; break
        if not oddil:
            # poslední „textová“ buňka
            for v in reversed(cols):
                if v and not is_num(v) and not is_year(v) and v != jmeno:
                    oddil = v; break
        if not jmeno:
            # nejdelší text mimo oddíl/čísla
            best = ""
            for v in cols:
                if v and v != oddil and not is_year(v):
                    # jméno bývá „nejhezčí“ text
                    if len(v) > len(best) and not re.search(r"^\d", v):
                        best = v
            jmeno = best

        # STR/STR stabil/STR+- – pokud chybí indexy, zkus najít čísla u konce řádku
        nums = [v for v in cols if is_num(v)]
        if not str_v and len(nums) >= 1:
            str_v = nums[-3] if len(nums) >= 3 else nums[-1]
        if not str_s and len(nums) >= 2:
            str_s = nums[-2] if len(nums) >= 2 else ""
        if not str_pm:
            # poslední „číslo“ může být +- (může být i záporné bez desetinné čárky)
            pm = ""
            for v in reversed(cols):
                vv = v.strip().replace(",", ".")
                if re.fullmatch(r"-?\d+(?:\.\d+)?", vv):
                    pm = v; break
            str_pm = pm

        out.append([poradi, jmeno, rok, oddil, zapasy, str_v, str_s, str_pm,
                    svaz, KAT.upper(), ROCNIK])
    return out

def click_next_if_any(page, table):
    candidates = [
        "a.paginate_button.next:not(.disabled)",
        "li.paginate_button.next:not(.disabled) a",
        "a[aria-label='Next']:not(.disabled)",
        "button[aria-label='Next']:not([disabled])",
    ]
    for sel in candidates:
        el = page.query_selector(sel)
        if el:
            tb = table.query_selector("tbody") or table
            cell = tb.query_selector("tr td, tr th")
            before = cell.inner_text().strip() if cell else ""
            el.click()
            try:
                page.wait_for_timeout(300)
                page.wait_for_function(
                    """(prev) => {
                        const tb = document.querySelector('table tbody') || document.querySelector('table');
                        const c = tb && tb.querySelector('tr td, tr th');
                        const now = c ? (c.textContent||'').trim() : '';
                        return now && now !== prev;
                    }""",
                    timeout=4000, arg=before
                )
            except PWTimeout:
                return False
            return True
    return False

def export_zebricek(page, svaz):
    url = f"{BASE}/zebricekstr-oblast/svaz-{svaz}/rocnik-{ROCNIK}/kategorie-{KAT}/zvyssich-{ZVYSS}"
    attempts = 6
    for a in range(1, attempts+1):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else None
        try:
            wait_rows_ready(page, timeout_ms=18000 + a*4000)
        except PWTimeout:
            pass

        table = find_best_table(page)
        rows_all = []
        if table:
            rows_all.extend(parse_page_rows(table, svaz))
            step = 0
            while click_next_if_any(page, table) and step < 80:
                table = find_best_table(page) or table
                rows_all.extend(parse_page_rows(table, svaz))
                step += 1

        if rows_all:
            outp = os.path.join(OUTDIR, f"zebricek_{svaz}_{ROCNIK}_kat-{KAT}.csv")
            with open(outp, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Poradi","Příjmení a jméno","Rok.nar.","Oddil","Zápasy","STR","STR stabil","STR+-","Svaz","Kategorie","Rocnik"])
                w.writerows(rows_all)
            print(f"{svaz}: žebříček ({KAT}) {len(rows_all)} řádků -> {outp}")
            return

        # debug + retry
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

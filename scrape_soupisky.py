# scrape_soupisky.py
# CSV hlavička:
# Oddil;P.č.;Příjmení a jméno;Rok.nar.;Umístění na žebříčku;Soutez

import os, csv, time, pathlib, re
from playwright.sync_api import sync_playwright

# ===== konfigurace =====
SVAZY   = ["420103", "420210"]                 # doplň dle potřeby
ROCNIK  = os.getenv("ROCNIK", "2025")
BASE    = "https://stis.ping-pong.cz"
OUTDIR  = "data"
DEBUG   = os.path.join(OUTDIR, "debug")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

pathlib.Path(OUTDIR).mkdir(parents=True, exist_ok=True)
pathlib.Path(DEBUG).mkdir(parents=True, exist_ok=True)

# ===== pomocné funkce =====
def cells_texts(row):
    # texty ze všech buněk (td i th), ořezané
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

def norm_poradi(s: str) -> str:
    m = re.search(r"\d+", s)
    return (m.group(0) + ".") if m else s

def looks_like_rank_any(s: str) -> bool:
    """povol běžné tvary umístění: 1., 21.-30., 21.-30.N, apod."""
    t = s.strip()
    if not t or is_year(t):  # rok to není
        return False
    return bool(
        re.fullmatch(r"\d+\.?", t) or
        re.fullmatch(r"\d+\s*[-–]\s*\d+\.?(?:[A-Za-z])?", t)
    )

def split_comp_and_rank(text: str):
    """
    'kraj. muži 14.' -> ('kraj. muži', '14.')
    pokud číslo na konci není, vrátí (text, '')
    """
    t = text.strip()
    m = re.search(r"(\d+)\.?\s*$", t)
    if m:
        comp = t[:m.start()].strip(" .,-")
        rank = m.group(1) + "."
        return comp, rank
    return t, ""

def find_table(page):
    sel = "table.soupisky, table.table.soupisky, table.table-bordered.soupisky"
    return page.query_selector(sel)

# ===== parser jedné tabulky =====
def parse_table(tbl):
    rows = tbl.query_selector_all("tr")
    y = 0
    oddil = ""
    soutez_text = ""
    predsoutez = ""
    header_rank = ""         # např. '14.' z hlavičky "kraj. muži 14."
    out = []

    for r in rows:
        cols = cells_texts(r)  # bere <td> i <th>
        if not cols:
            continue

        first = cols[0].strip()

        # Datový řádek – první buňka je pořadí (číslo)
        if first[:1].isdigit() and re.match(r"^\d+\.?$", first):
            poradi = norm_poradi(first)

            # zjisti rok narození + celé jméno (nedělíme)
            year_idx = next((i for i, v in enumerate(cols) if is_year(v)), -1)
            rocnik = ""
            cele_jmeno = ""
            umisteni = ""

            if year_idx >= 0:
                rocnik = cols[year_idx].strip()
                # jméno bývá těsně před rokem; fallback na další sloupce
                if year_idx - 1 >= 1:
                    cele_jmeno = cols[year_idx - 1].strip()
                elif len(cols) > 2:
                    cele_jmeno = cols[2].strip()
                elif len(cols) > 1:
                    cele_jmeno = cols[1].strip()

                # ⬅️ KLÍČOVÁ ZMĚNA: vezmi umístění přímo z buňky ZA rokem (bez filtru)
                if year_idx + 1 < len(cols):
                    umisteni = cols[year_idx + 1].strip()

            # fallbacky
            if not cele_jmeno:
                cele_jmeno = cols[2].strip() if len(cols) > 2 else (cols[1].strip() if len(cols) > 1 else "")

            if not umisteni:
                # hledej kdekoli v řádku položku vypadající jako umístění (kromě 1. sloupce P.č.)
                for idx, v in enumerate(cols):
                    if idx == 0:
                        continue
                    if looks_like_rank_any(v):
                        umisteni = v.strip()
                        break

            if not umisteni and header_rank:
                umisteni = header_rank  # použij číslo z hlavičky, např. '14.'

            out.append([
                oddil,          # Oddil
                poradi,         # P.č.
                cele_jmeno,     # Příjmení a jméno (v jednom poli)
                rocnik,         # Rok.nar.
                umisteni,       # Umístění na žebříčku
                soutez_text     # Soutez (bez čísla)
            ])
            y = 2
            continue

        # ---- hlavičky (oddíl / soutěž) – čteme i <th> ----
        y += 1
        val = first
        if   y == 1:
            pass                              # „Soupiska…“ – neukládáme
        elif y == 2:
            # řádek se soutěží – může mít i pořadí na konci (kraj. muži 14.)
            comp, rank = split_comp_and_rank(val)
            soutez_text = comp                # např. 'kraj. muži' / 'Divize'
            header_rank = rank or header_rank # např. '14.' (pro fallback)
        elif y == 3:
            oddil  = val
        elif y == 4:                          # někdy se prohodí
            predsoutez, soutez_text, oddil = soutez_text, oddil, val
        elif y == 5:
            oddil = val
            soutez_text = predsoutez
            y = 4

    return out

# ===== warmup + export jednoho svazu s retry =====
def warmup(page):
    page.set_extra_http_headers({"Accept-Language":"cs,en;q=0.8"})
    page.set_default_timeout(45000)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)

def export_svaz(page, svaz):
    url = f"{BASE}/soupisky/svaz-{svaz}/rocnik-{ROCNIK}"
    attempts = 8
    for a in range(1, attempts+1):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else None
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass
        page.wait_for_timeout(800 + 200*a)

        tbl = find_table(page)
        if tbl:
            rows = parse_table(tbl)
            if rows:
                outp = os.path.join(OUTDIR, f"soupisky_{svaz}_{ROCNIK}.csv")
                with open(outp, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["Oddil","P.č.","Příjmení a jméno","Rok.nar.","Umístění na žebříčku","Soutez"])
                    w.writerows(rows)
                print(f"{svaz}: {len(rows)} řádků -> {outp}")
                return

        # --- debug + další pokus ---
        html = page.content()
        with open(os.path.join(DEBUG, f"svaz_{svaz}_attempt{a}_status{status or 0}.html"),
                  "w", encoding="utf-8") as f:
            f.write(html)
        try:
            page.screenshot(path=os.path.join(DEBUG, f"svaz_{svaz}_attempt{a}.png"),
                            full_page=True)
        except:
            pass
        time.sleep(0.6 + 0.4*a)

    raise RuntimeError(f"Nenalezena tabulka pro svaz {svaz} po {attempts} pokusech")

# ===== main =====
def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent=UA,
            viewport={"width":1366, "height":900},
            locale="cs-CZ"
        )
        page = context.new_page()
        warmup(page)
        for svaz in SVAZY:
            export_svaz(page, svaz)
        browser.close()

if __name__ == "__main__":
    main()

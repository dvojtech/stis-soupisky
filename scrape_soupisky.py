import os, csv, time, pathlib
from playwright.sync_api import sync_playwright

# ----- konfigurace -----
SVAZY   = ["420103", "420210"]
ROCNIK  = os.getenv("ROCNIK", "2025")
BASE    = "https://stis.ping-pong.cz"
OUTDIR  = "data"
DEBUG   = os.path.join(OUTDIR, "debug")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

pathlib.Path(OUTDIR).mkdir(parents=True, exist_ok=True)
pathlib.Path(DEBUG).mkdir(parents=True, exist_ok=True)

def itxt(n):
    try:
        return (n.inner_text() or "").strip()
    except:
        return ""

def find_table(page):
    sel = "table.soupisky, table.table.soupisky, table.table-bordered.soupisky"
    return page.query_selector(sel)

def parse_table(tbl):
    rows = tbl.query_selector_all("tr")
    y=0; oddil=""; soutez=""; predsoutez=""
    out=[]
    for r in rows:
        tds = r.query_selector_all("td")
        if not tds: continue
        first = itxt(tds[0])
        if first[:1] in "123456789":
            out.append([
                oddil,
                first + ".",
                itxt(tds[2]) if len(tds)>2 else "",
                itxt(tds[3]) if len(tds)>3 else "",
                itxt(tds[4]) if len(tds)>4 else "",
                soutez
            ])
            y=2
        else:
            y+=1
            if   y==1: _soupiska = itxt(tds[0])
            elif y==2: soutez    = itxt(tds[0])
            elif y==3: oddil     = itxt(tds[0])
            elif y==4: predsoutez, soutez, oddil = soutez, oddil, itxt(tds[0])
            elif y==5: oddil = itxt(tds[0]); soutez = predsoutez; y=4
    return out

def warmup(page):
    # získá PHPSESSID a případné cookies
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
        # Počkej na dojetí XHR; na STIS někdy chodí 202 a DOM doběhne až po chvilce
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass
        page.wait_for_timeout(800)

        tbl = find_table(page)
        if tbl:
            rows = parse_table(tbl)
            if rows:
                out = os.path.join(OUTDIR, f"soupisky_{svaz}_{ROCNIK}.csv")
                with open(out, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["oddil","poradi","prijmeni","jmeno","rocnik","soutez"])
                    w.writerows(rows)
                print(f"{svaz}: {len(rows)} řádků -> {out}")
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
        # 202/placeholder -> krátká pauza a zkusit znovu
        time.sleep(0.8 + 0.4*a)

    raise RuntimeError(f"Nenalezena tabulka pro svaz {svaz} po {attempts} pokusech")

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent=UA,
            viewport={"width":1366,"height":900},
            locale="cs-CZ"
        )
        page = context.new_page()
        warmup(page)
        for svaz in SVAZY:
            export_svaz(page, svaz)
        browser.close()

if __name__ == "__main__":
    main()

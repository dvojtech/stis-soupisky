# Export soupisky do CSV: oddil;poradi;prijmeni;jmeno;rocnik;soutez
import os, csv
from playwright.sync_api import sync_playwright

SVAZY = ["420103", "420210"]                 # RSST PZ + PS Praha
ROCNIK = os.getenv("ROCNIK", "2025")
OUTDIR = "data"
BASE = "https://stis.ping-pong.cz"

os.makedirs(OUTDIR, exist_ok=True)

def t(n):  # safe inner text
    try: s = n.inner_text().strip()
    except: s = ""
    return s

def parse_table(page):
    sel = "table.soupisky, table.table.soupisky, table.table-bordered.soupisky"
    page.wait_for_selector(sel, timeout=20000)
    rows = page.query_selector(sel).query_selector_all("tr")
    y=0; oddil=""; soutez=""; predsoutez=""
    out=[]
    for r in rows:
        tds = r.query_selector_all("td")
        if not tds: continue
        first = t(tds[0])
        if first[:1] in "123456789":
            out.append([
                oddil,
                first + ".",
                t(tds[2]) if len(tds)>2 else "",
                t(tds[3]) if len(tds)>3 else "",
                t(tds[4]) if len(tds)>4 else "",
                soutez
            ])
            y=2
        else:
            y+=1
            if y==1:
                _soupiska = t(tds[0])
            elif y==2:
                soutez = t(tds[0])
            elif y==3:
                oddil = t(tds[0])
            elif y==4:
                predsoutez = soutez
                soutez = oddil
                oddil = t(tds[0])
            elif y==5:
                oddil = t(tds[0]); soutez = predsoutez; y=4
    return out

def export_svaz(page, svaz):
    url = f"{BASE}/soupisky/svaz-{svaz}/rocnik-{ROCNIK}"
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(1200)
    rows = parse_table(page)
    path = os.path.join(OUTDIR, f"soupisky_{svaz}_{ROCNIK}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["oddil","poradi","prijmeni","jmeno","rocnik","soutez"])
        w.writerows(rows)
    print(f"{svaz}: {len(rows)} řádků -> {path}")

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        for svaz in SVAZY:
            export_svaz(page, svaz)
        browser.close()

if __name__ == "__main__":
    main()

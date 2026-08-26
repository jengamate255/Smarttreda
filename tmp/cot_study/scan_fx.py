# -*- coding: utf-8 -*-
"""List all FX-ish market names in current file + sample the exact WTI row."""
import urllib.request, csv

req = urllib.request.Request("https://www.cftc.gov/dea/newcot/f_disagg.txt",
                             headers={"User-Agent": "Mozilla/5.0"})
raw = urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "ignore")
rows = list(csv.reader(raw.splitlines()))
print("total rows:", len(rows))
print("header cols:", len(rows[0]))
fxlike = []
for r in rows[1:]:
    if not r:
        continue
    n = r[0].upper()
    if any(k in n for k in ["EURO", "POUND", "STERLING", "SWISS", "YEN", "CURRENCY", " FX ", "DOLLAR INDEX"]):
        fxlike.append(r[0])
print("FX-like markets:", len(fxlike))
for n in sorted(set(fxlike)):
    print("   ", n)

# header check: find MM columns
hdr = rows[0]
for i, h in enumerate(hdr):
    if "M_MONEY" in h.upper() or "AS_OF" in h.upper() or "REPORT_DATE" in h.upper() or "OPEN_INTEREST_ALL" in h.upper():
        print(i, h)
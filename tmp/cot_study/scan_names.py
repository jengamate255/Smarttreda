# -*- coding: utf-8 -*-
"""Scan market names for our 4 instruments in 2025 zip and current 2026 file."""
import io, zipfile, urllib.request, csv, collections

def get_names(filebytes):
    z = zipfile.ZipFile(io.BytesIO(filebytes))
    txt = [n for n in z.namelist() if n.lower().endswith(".txt")][0]
    raw = z.read(txt).decode("utf-8", "ignore")
    return raw

urls = {
    "2025": "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2025.zip",
    "2026current": "https://www.cftc.gov/dea/newcot/f_disagg.txt",
}
pats = ["EURO FX", "BRITISH POUND", "GOLD", "CRUDE"]
for label, u in urls.items():
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=180).read()
    raw = get_names(data) if label == "2025" else data.decode("utf-8", "ignore")
    names = set()
    for row in csv.reader(raw.splitlines()):
        if not row:
            continue
        n = row[0].upper()
        if any(p in n for p in pats) and ("EXCHANGE" in n or "COME" in n or "NYMEX" in n or "ICE" in n):
            names.add(n)
    print(f"=== {label}: {len(names)} matching markets ===")
    for n in sorted(names):
        print("   ", n)
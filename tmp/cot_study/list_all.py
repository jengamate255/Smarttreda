# -*- coding: utf-8 -*-
"""List ALL market names in the 2025 annual zip and the current weekly file."""
import io, zipfile, urllib.request, csv

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=180).read()

for label, url in [
    ("2025zip", "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2025.zip"),
    ("current", "https://www.cftc.gov/dea/newcot/f_disagg.txt"),
]:
    data = fetch(url)
    if label == "2025zip":
        z = zipfile.ZipFile(io.BytesIO(data))
        txt = [n for n in z.namelist() if n.lower().endswith(".txt")][0]
        raw = z.read(txt).decode("utf-8", "ignore")
    else:
        raw = data.decode("utf-8", "ignore")
    rows = list(csv.reader(raw.splitlines()))
    names = sorted(set(r[0] for r in rows[1:] if r and r[0].strip()))
    print(f"=== {label}: {len(names)} distinct markets ===")
    for n in names:
        print("   ", n)
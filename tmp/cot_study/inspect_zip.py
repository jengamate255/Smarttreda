# -*- coding: utf-8 -*-
"""Download one annual zip and inspect internal file format."""
import io, zipfile, urllib.request, os

os.makedirs("raw", exist_ok=True)
url = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2025.zip"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
data = urllib.request.urlopen(req, timeout=120).read()
with open(os.path.join("raw", "fut_disagg_txt_2025.zip"), "wb") as f:
    f.write(data)

z = zipfile.ZipFile(io.BytesIO(data))
names = z.namelist()
print("files in zip:", len(names))
print(names[:5])
print(names[-5:])

# inspect first text file
txt = [n for n in names if n.lower().endswith(".txt")][0]
print("sample file:", txt)
raw = z.read(txt).decode("utf-8", "ignore")
lines = raw.splitlines()
print("total lines:", len(lines))
print("--- header ---")
print(lines[0][:2000])
print("--- row 1 ---")
print(lines[1][:2000])

# check if EURO FX and WTI lines exist
for ln in lines:
    if "EURO FX" in ln and "CHICAGO" in ln:
        print("--- EURO FX row ---")
        print(ln[:1500])
        break
for ln in lines:
    if ("WTI CRUDE" in ln or "CRUDE OIL" in ln) and "MERCANTILE" in ln:
        print("--- OIL row ---")
        print(ln[:1500])
        break
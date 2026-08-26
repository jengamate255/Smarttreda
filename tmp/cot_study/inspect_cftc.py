# -*- coding: utf-8 -*-
"""Probe CFTC historical compressed page and list disaggregated futures links."""
import re, urllib.request

url = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed.htm"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
html = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
print("page bytes:", len(html))

# find all hrefs mentioning disagg (futures-only disaggregated)
links = sorted(set(re.findall(r'href="([^"]*fut_disagg[^"]*\.zip)"', html, re.I)))
print("num fut_disagg zip links:", len(links))
for l in links[:40]:
    print(l)

# also show a couple of generic zip links to learn URL structure
generic = sorted(set(re.findall(r'href="([^"]*history[^"]*\.zip)"', html, re.I)))
print("num history zip links:", len(generic))
for l in generic[:10]:
    print(l)
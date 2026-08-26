# -*- coding: utf-8 -*-
"""Probe candidate CFTC URLs to find working data sources."""
import urllib.request

urls = [
    "https://www.cftc.gov/dea/newcot/f_disagg.txt",                 # current weekly disaggregated
    "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2025.zip",  # annual zip guess
    "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2026.zip",
    "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
    "https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed.htm",
]

for u in urls:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=40)
        body = r.read()
        print(f"OK   {u}  -> status {r.status}, {len(body)} bytes")
        if len(body) < 4000:
            print(body[:500])
    except Exception as e:
        print(f"FAIL {u}  -> {e}")
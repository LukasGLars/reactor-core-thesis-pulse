import requests, os, json
key = os.environ.get("EIA_API","")
# List available series for petroleum futures
url = "https://api.eia.gov/v2/petroleum/pri/fut/facet/series/?api_key=" + key
r = requests.get(url, timeout=15)
print("status:", r.status_code)
d = r.json()
rows = d.get("response",{}).get("facets",[])
for row in rows[:30]:
    print(row)

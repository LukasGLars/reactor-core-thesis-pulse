import requests, os, json
key = os.environ.get("EIA_API","")
# First: check what facets/series are available for petroleum futures
meta_url = "https://api.eia.gov/v2/petroleum/pri/fut/?api_key=" + key
r = requests.get(meta_url, timeout=15)
print("meta status:", r.status_code)
d = r.json()
# Print facets
facets = d.get("response",{}).get("facets",[])
for f in facets:
    print("facet:", json.dumps(f))

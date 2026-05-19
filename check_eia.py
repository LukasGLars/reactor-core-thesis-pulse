import requests, os
key = os.environ.get("EIA_API","")
url = "https://api.eia.gov/v2/petroleum/pri/fut/data/"
params = {
    "api_key": key,
    "frequency": "daily",
    "data[0]": "value",
    "facets[series][]": "RCLC12",
    "start": "2020-01-01",
    "sort[0][column]": "period",
    "sort[0][direction]": "asc",
    "length": 5,
}
r = requests.get(url, params=params, timeout=15)
d = r.json()
resp = d.get("response",{})
print("total:", resp.get("total"))
for row in resp.get("data",[]):
    print(row)

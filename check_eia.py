import requests, os
key = os.environ.get("EIA_API","")
url = "https://api.eia.gov/v2/petroleum/pri/fut/data/"
# Check RCLC1 and RCLC4 - spot and 4-month forward
for series in ["RCLC1","RCLC4"]:
    params = {
        "api_key": key, "frequency": "daily",
        "data[0]": "value", "facets[series][]": series,
        "sort[0][column]": "period", "sort[0][direction]": "asc",
        "length": 3,
    }
    r = requests.get(url, params=params, timeout=15)
    resp = r.json().get("response",{})
    rows = resp.get("data",[])
    print("{}: total={} earliest={}".format(series, resp.get("total"), rows[0].get("period") if rows else "none"))

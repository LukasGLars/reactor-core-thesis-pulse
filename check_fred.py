import requests, os
key = os.environ.get("FRED_API_KEY","")
for sid in ["DBAA","DAAA"]:
    url = "https://api.stlouisfed.org/fred/series?series_id={}&api_key={}&file_type=json".format(sid, key)
    s = requests.get(url, timeout=10).json().get("seriess",[{}])[0]
    print("{}: start={} freq={}".format(sid, s.get("observation_start"), s.get("frequency_short")))

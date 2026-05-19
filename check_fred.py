import requests, os
key = os.environ.get("FRED_API_KEY","")
for sid in ["BAMLH0A0HYM2","BAMLC0A0CM","BAA"]:
    url = "https://api.stlouisfed.org/fred/series?series_id={}&api_key={}&file_type=json".format(sid, key)
    s = requests.get(url, timeout=10).json().get("seriess",[{}])[0]
    start = s.get("observation_start")
    freq  = s.get("frequency_short")
    print("{}: start={} freq={}".format(sid, start, freq))

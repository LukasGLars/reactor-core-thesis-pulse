import requests, os, json
key = os.environ.get("FRED_API_KEY","")
for sid in ["BAMLH0A0HYM2","BAMLC0A0CM","BAA"]:
    url = f"https://api.stlouisfed.org/fred/series?series_id={sid}&api_key={key}&file_type=json"
    d = requests.get(url,timeout=10).json().get("seriess",[{}])[0]
    print(f"{sid}: start={d.get(\"observation_start\")} freq={d.get(\"frequency_short\")}")

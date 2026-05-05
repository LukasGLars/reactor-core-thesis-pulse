import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TOTAL  = 996365
USDSEK = 9.39431

# v3 weights — Silver held as cash pending gold/silver ratio < 55
# Cash: 2% structural + 10% silver-pending = 12% total
assets = [
    ("Gold (Guld AVA)", "xauusd",  0.250, None,   1125.09),
    ("Walmart",         "wmt.us",  0.150, 124.28,  None),
    ("Eli Lilly",       "lly.us",  0.150, 919.77,  None),
    ("Cameco",          "ccj.us",  0.100, 108.61,  None),
    ("Vertiv",          "vrt.us",  0.100, 250.58,  None),
    ("Broadcom",        "avgo.us", 0.080, 309.51,  None),
    ("J&J",             "jnj.us",  0.050, 244.44,  None),
]
SILVER_PENDING = 0.100   # held as cash until ratio < 55
CASH_STRUCTURAL = 0.020  # permanent floor
CASH_TOTAL = SILVER_PENDING + CASH_STRUCTURAL

data = []
for name, ticker, weight, usd, sek_override in assets:
    sek    = sek_override if sek_override else round(usd * USDSEK, 2)
    target = TOTAL * weight
    shares = int(target // sek)
    frac   = (target % sek) / sek
    data.append({"name":name,"ticker":ticker,"weight":weight,
                 "sek":sek,"usd":usd,"target":target,"shares":shares,"frac":frac})

# Greedy: add 1 share to highest-fractional positions while budget allows
base_cost = sum(d["shares"] * d["sek"] for d in data)
remaining = TOTAL - base_cost
for i in sorted(range(len(data)), key=lambda i: data[i]["frac"], reverse=True):
    if remaining >= data[i]["sek"]:
        data[i]["shares"] += 1
        remaining -= data[i]["sek"]

print(f"Total capital: {TOTAL:,.0f} kr  |  USD/SEK: {USDSEK}  |  Prices: 2026-03-31")
print(f"Silver: NOT deployed — awaiting gold/silver ratio < 55 (current: ~64)")
print()
print(f"{'Asset':<16} {'USD Price':>10}  {'SEK Price':>10}  {'Weight':>7}  {'Shares':>7}  {'Cost kr':>10}  {'Actual%':>8}")
print("-" * 80)
total_cost = 0
for d in data:
    cost       = d["shares"] * d["sek"]
    actual_pct = cost / TOTAL * 100
    usd_str    = f"${d['usd']:,.2f}" if d["usd"] else "SEK-native"
    print(f"{d['name']:<16} {usd_str:>10}  {d['sek']:>10,.2f}  {d['weight']*100:>6.1f}%  "
          f"{d['shares']:>7,}  {cost:>10,.0f}  {actual_pct:>7.1f}%")
    total_cost += cost

total_cash = TOTAL - total_cost
deployed_w = sum(d["weight"] for d in data)

print("-" * 80)
print(f"{'Deployed':<16} {'':>10}  {'':>10}  {deployed_w*100:>6.1f}%  {'':>7}  {total_cost:>10,.0f}  {total_cost/TOTAL*100:>7.1f}%")
print(f"{'Cash':<16} {'':>10}  {'':>10}  {total_cash/TOTAL*100:>6.1f}%  {'':>7}  {total_cash:>10,.0f}  {total_cash/TOTAL*100:>7.1f}%")
print("-" * 80)
print()
print(f"Cash earmarked:")
print(f"  Silver-pending (10%): ~{TOTAL*SILVER_PENDING:>9,.0f} kr  — deploy when ratio < 55")
print(f"  Structural (2%):      ~{TOTAL*CASH_STRUCTURAL:>9,.0f} kr  — permanent floor")
print(f"  Total cash:            {total_cash:>9,.0f} kr  ({total_cash/TOTAL*100:.1f}%)")
print()
print(f"Note: prices from 2026-03-31 CSVs. Update before executing.")
print(f"Note: silver (xagusd) held as cash until gold/silver ratio < 55 (current: ~64).")

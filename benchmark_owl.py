"""Benchmark gate: scenario A in Owl (MILP) vs our DP/coordinate-ascent optimum.

NOTE: requires the `owlplanner` package (pip install owlplanner), not listed
in requirements.txt's core deps since it's only needed for this script.

Owl: github.com/mdlacasse/Owl (GPL — benchmark use only, never ship).
Run with a python that has owlplanner installed.

Alignment with scenario_a.py:
  couple born 1966/1968 (60/58 in 2026), horizon to elder age 90,
  Trad $1.5M / taxable $600k (90% basis) / Roth $200k, all owned by elder,
  net spending $70k/yr real (flat), SS $30k each at 70,
  4% real return on everything (6.6% nominal, 2.5% inflation),
  ACA SLCSP $22.6k/yr household, heir tax 24% on tax-deferred,
  federal only (no state).
"""

import owlplanner as owl

p = owl.Plan(["A", "B"], ["1966-01-01", "1968-01-01"], [90, 88], "scenarioA",
             verbose=True)
p.setAccountBalances(taxable=[600, 0], taxDeferred=[1500, 0], taxFree=[200, 0],
                     startDate="2026-01-01")
p.setCostBasis([540, 0])
# fixed nominal rates: (1.04 * 1.025 - 1) = 6.6% on all assets, 2.5% inflation
p.setRates("user", values=[6.6, 6.6, 6.6, 2.5])
p.setAllocationRatios("individual",
                      generic=[[[100, 0, 0, 0], [100, 0, 0, 0]],
                               [[100, 0, 0, 0], [100, 0, 0, 0]]])
p.setSpendingProfile("flat", percent=100)
# pias are MONTHLY PIA in $ (at FRA 67); $2,016/mo * 12 * 1.24 ~= $30k/yr at 70
p.setSocialSecurity([2016, 2016], [70, 70])
p.setHeirsTaxRate(24)
p.setDividendRate(2.0)
p.setACA(22.6)
p.setStateTax("")

p.solve("maxBequest", options={"netSpending": 70})
print(p.summaryString())

import numpy as np

print("\nOwl Roth conversion schedule (nominal $, nonzero years):")
for cand in ("myRothX_in", "x_in"):
    arr = getattr(p, cand, None)
    if arr is None:
        continue
    a = np.asarray(arr)
    tot = a.sum(axis=0) if a.ndim == 2 else a
    for n, v in enumerate(tot):
        if v > 1:
            print(f"  {p.year_n[n]}: {cand}={v:,.0f}")
    break

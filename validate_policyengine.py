"""Cross-validate scenario_a.py federal tax math against PolicyEngine US.

NOTE: requires the `policyengine-us` package (pip install policyengine-us),
not listed in requirements.txt's core deps since it's only needed for this
script.

CI-oracle only (PolicyEngine is AGPL-3.0 — never ship in product).
Compares federal income tax (incl. LTCG stacking + NIIT) for a 2026 MFJ
couple across income mixes typical of scenario A years.

Run with a python that has policyengine-us installed.
"""

import numpy as np
from policyengine_us import Simulation

import scenario_a as m

CASES = [
    # (ira_distribution i.e. conversion, qualified dividends, LTCG, ages)
    (0, 12_000, 5_000, (60, 58)),
    (69_000, 12_000, 5_000, (60, 58)),
    (120_000, 12_000, 8_000, (61, 59)),
    (300_000, 12_000, 10_000, (62, 60)),
    (415_000, 10_000, 20_000, (60, 58)),
    (595_000, 8_000, 15_000, (63, 61)),
    (0, 10_000, 0, (66, 64)),
    (50_000, 10_000, 5_000, (67, 65)),
]


def ours(conv, qdiv, ltcg, ages):
    n65 = sum(a >= 65 for a in ages)
    agi = conv + qdiv + ltcg
    ded = m.std_deduction(n65, 2026, agi)
    ti_ord = max(0.0, conv - ded)
    tax = m.ordinary_tax(ti_ord) + m.ltcg_tax(ti_ord, ltcg + qdiv)
    nii = qdiv + ltcg
    tax += m.NIIT_RATE * max(0.0, min(nii, agi - m.NIIT_THRESH))
    return tax


def theirs(conv, qdiv, ltcg, ages):
    sim = Simulation(situation={
        "people": {
            "p1": {"age": {2026: ages[0]},
                   "taxable_ira_distributions": {2026: conv},
                   "qualified_dividend_income": {2026: qdiv},
                   "long_term_capital_gains": {2026: ltcg}},
            "p2": {"age": {2026: ages[1]}},
        },
        "tax_units": {"tu": {"members": ["p1", "p2"]}},
        "families": {"f": {"members": ["p1", "p2"]}},
        "spm_units": {"s": {"members": ["p1", "p2"]}},
        "households": {"h": {"members": ["p1", "p2"],
                             "state_name": {2026: "TX"}}},
        "marital_units": {"m": {"members": ["p1", "p2"]}},
    })
    return float(sim.calculate("income_tax", 2026)[0])


if __name__ == "__main__":
    print(f"{'conv':>8}{'qdiv':>8}{'ltcg':>8}{'ages':>10}"
          f"{'ours':>12}{'policyengine':>14}{'diff':>10}")
    worst = 0.0
    for conv, qdiv, ltcg, ages in CASES:
        a, b = ours(conv, qdiv, ltcg, ages), theirs(conv, qdiv, ltcg, ages)
        worst = max(worst, abs(a - b))
        print(f"{conv:>8,}{qdiv:>8,}{ltcg:>8,}{str(ages):>10}"
              f"{a:>12,.0f}{b:>14,.0f}{a - b:>+10,.0f}")
    print(f"\nmax abs diff: ${worst:,.0f}")

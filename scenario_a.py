"""Scenario A (Stage 1, deterministic): early-retired couple x ACA cliff.

Couple aged 60/58 at start of 2026, retired. Trad IRA $1.5M / taxable $600k
(high basis) / Roth $200k. Living expense $70k/yr (real). All in real 2026
dollars; indexed tax constants held fixed in real terms, non-indexed
thresholds (SS provisional, NIIT) deflated at assumed inflation.

Policies compared:
  1. no conversion
  2. fill 12% bracket
  3. fill 24% bracket
  4. cliff-aware greedy (MAGI just under 400% FPL while on ACA, then fill 24%)
  5. optimized (coordinate ascent over per-year conversion amounts)

Constants: see constants-2026.md (sources therein).
"""

import numpy as np

# ---- 2026 tax constants (MFJ, real 2026$) ----
STD_DED = 32_200.0
BRACKETS = [(0, .10), (24_800, .12), (100_800, .22), (211_400, .24),
            (403_550, .32), (512_450, .35), (768_700, .37)]
LTCG_0_TOP = 98_900.0
LTCG_15_TOP = 613_700.0
NIIT_THRESH = 250_000.0   # not indexed -> deflated
NIIT_RATE = 0.038
SS_T1, SS_T2 = 32_000.0, 44_000.0  # not indexed -> deflated
FPL400 = 84_600.0         # 2-person household, 2026 coverage
# ACA applicable percentage (Rev. Proc. 2025-25), piecewise linear on %FPL
ACA_PTS = [(1.33, .0314), (1.50, .0419), (2.00, .0660), (2.50, .0844),
           (3.00, .0996), (4.00, .0996)]
# IRMAA (MFJ): MAGI tier floors, per-person annual cost (PartB+PartD surch x12)
IRMAA_TIERS = [(218_000, (81.20 + 14.50) * 12), (274_000, (202.90 + 37.50) * 12),
               (342_000, (324.60 + 60.40) * 12), (410_000, (446.30 + 83.30) * 12),
               (750_000, (487.00 + 91.00) * 12)]
RMD_DIVISOR = {73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0,
               79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7, 84: 16.8,
               85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2}

# ---- scenario parameters ----
R = 0.04            # real return, all accounts
INFL = 0.025        # used only to deflate non-indexed thresholds
YEARS = 31          # elder age 60..90
AGE1_0, AGE2_0 = 60, 58
TRAD0, TAXABLE0, ROTH0 = 1_500_000.0, 600_000.0, 200_000.0
SPEND = 70_000.0
DIV_YIELD = 0.02    # qualified dividends on taxable
GAIN_FRAC = 0.10    # realized LTCG fraction of taxable sales (high basis)
SS_EACH = 30_000.0  # each claims at own age 70 (real $)
ACA_PREMIUM_COUPLE = 22_600.0  # national avg benchmark silver, 60yo couple (KFF)
RMD_START = 75      # born after 1960
HEIR_RATE = 0.24    # heir marginal rate on inherited Trad; taxable gets step-up


EXTRA_STD_65 = 1_650.0   # per spouse 65+ (MFJ, 2026)
OBBBA_SENIOR = 6_000.0   # per spouse 65+, TY2025-2028 only, 6% phase-out >$150k MAGI


def std_deduction(n65, year, magi):
    ded = STD_DED + EXTRA_STD_65 * n65
    if year <= 2028 and n65:
        ded += max(0.0, OBBBA_SENIOR * n65 - 0.06 * max(0.0, magi - 150_000.0))
    return ded


def ordinary_tax(ti):
    tax, prev = 0.0, None
    for i, (lo, rate) in enumerate(BRACKETS):
        hi = BRACKETS[i + 1][0] if i + 1 < len(BRACKETS) else float("inf")
        if ti > lo:
            tax += (min(ti, hi) - lo) * rate
    return tax


def ltcg_tax(ord_ti, ltcg):
    """LTCG stacked on top of ordinary taxable income."""
    tax = 0.0
    lo, hi = max(ord_ti, 0.0), max(ord_ti, 0.0) + ltcg
    tax += max(0.0, min(hi, LTCG_15_TOP) - max(lo, LTCG_0_TOP)) * 0.15
    tax += max(0.0, hi - max(lo, LTCG_15_TOP)) * 0.20
    return tax


def ss_taxable(ss, other_agi, defl):
    if ss <= 0:
        return 0.0
    t1, t2 = SS_T1 * defl, SS_T2 * defl
    prov = other_agi + 0.5 * ss
    if prov <= t1:
        return 0.0
    if prov <= t2:
        return min(0.5 * (prov - t1), 0.5 * ss)
    return min(0.85 * ss, 0.85 * (prov - t2) + min(0.5 * (t2 - t1), 0.5 * ss))


def aca_subsidy(magi, premium):
    fpl = magi / (FPL400 / 4.0)
    if fpl > 4.0 or fpl < 1.0:
        return 0.0  # the cliff / below PTC floor (Medicaid territory)
    if fpl <= ACA_PTS[0][0]:
        pct = 0.021
    else:
        pct = ACA_PTS[-1][1]
        for (x0, y0), (x1, y1) in zip(ACA_PTS, ACA_PTS[1:]):
            if fpl <= x1:
                pct = y0 + (y1 - y0) * (fpl - x0) / (x1 - x0)
                break
    return max(0.0, premium - pct * magi)


def irmaa_cost(magi_2ago, n_enrolled):
    cost = 0.0
    for floor, annual in IRMAA_TIERS:
        if magi_2ago > floor:
            cost = annual
    return cost * n_enrolled


def simulate(conversions, premium=ACA_PREMIUM_COUPLE, heir_rate=HEIR_RATE,
             detail=False):
    """conversions: array of len YEARS, requested Roth conversion per year."""
    trad, taxable, roth = TRAD0, TAXABLE0, ROTH0
    magi_hist = [0.0, 0.0]  # 2-year lookback; pre-retirement MAGI assumed low
    rows = []
    for t in range(YEARS):
        a1, a2 = AGE1_0 + t, AGE2_0 + t
        defl = 1.0 / (1 + INFL) ** t
        conv = min(conversions[t], trad)
        ss = SS_EACH * ((a1 >= 70) + (a2 >= 70))
        rmd = trad / RMD_DIVISOR[min(a1, 90)] if a1 >= RMD_START else 0.0
        div = DIV_YIELD * taxable
        n_aca = (a1 < 65) + (a2 < 65)
        prem = premium * (n_aca / 2.0)
        n_med = (a1 >= 65) + (a2 >= 65)
        irmaa = irmaa_cost(magi_hist[-2], n_med)

        # fixed-point: withdrawals/sales needed depend on tax and vice versa
        extra_wd = 0.0  # trad withdrawal beyond RMD (used once taxable dries up)
        sale = 0.0
        for _ in range(8):
            ord_inc = conv + rmd + extra_wd
            ltcg = GAIN_FRAC * sale
            ss_tax_inc = ss_taxable(ss, ord_inc + div + ltcg, defl)
            agi = ord_inc + div + ltcg + ss_tax_inc
            magi = ord_inc + div + ltcg + ss  # ACA MAGI adds untaxed SS
            ded = std_deduction(n_med, 2026 + t, agi)
            ti_ord = max(0.0, ord_inc + ss_tax_inc - ded)
            # dividends are qualified: taxed with LTCG stack
            fed = ordinary_tax(ti_ord) + ltcg_tax(ti_ord, ltcg + div)
            nii = div + ltcg
            fed += NIIT_RATE * max(0.0, min(nii, agi - NIIT_THRESH * defl))
            subsidy = aca_subsidy(magi, premium) * (n_aca / 2.0) if n_aca else 0.0
            need = SPEND + fed + irmaa + max(0.0, prem - subsidy) - div - ss \
                - rmd - extra_wd + conv * 0.0
            # conversion tax must also be funded; it's inside `fed` already
            if need <= 0:
                sale = 0.0
                break
            if need <= taxable:
                sale = need
            else:
                sale = taxable
                extra_wd = min(need - taxable, max(0.0, trad - conv - rmd))
        # any remaining shortfall comes out of Roth (tax-free)
        roth_wd = max(0.0, need - sale - extra_wd) if need > 0 else 0.0
        magi_hist.append(magi)
        if detail:
            rows.append(dict(year=2026 + t, a1=a1, conv=conv, fed=fed,
                             magi=magi, subsidy=subsidy, irmaa=irmaa,
                             trad=trad, taxable=taxable, roth=roth))
        trad = (trad - conv - rmd - extra_wd) * (1 + R)
        roth = max(0.0, roth + conv - roth_wd) * (1 + R)
        surplus = -need if need <= 0 else 0.0
        taxable = max(0.0, taxable - sale + surplus) * (1 + R)
    estate = roth + taxable + trad * (1 - heir_rate)
    return (estate, rows) if detail else estate


# ---- policies ----
def policy_fill_bracket(top):
    """Convert so ordinary taxable income reaches bracket top each year, while trad>0."""
    convs = np.zeros(YEARS)
    trad, taxable = TRAD0, TAXABLE0
    for t in range(YEARS):
        a1 = AGE1_0 + t
        rmd = trad / RMD_DIVISOR[min(a1, 90)] if a1 >= RMD_START else 0.0
        ss = SS_EACH * ((a1 >= 70) + (AGE2_0 + t >= 70))
        base = rmd + 0.85 * ss  # rough ordinary income floor
        room = max(0.0, top + STD_DED - base)
        convs[t] = min(room, trad)
        trad = max(0.0, trad - convs[t] - rmd) * (1 + R)
    return convs


def policy_cliff_greedy():
    """Keep MAGI just under 400% FPL while on ACA; fill 24% after."""
    convs = np.zeros(YEARS)
    trad = TRAD0
    for t in range(YEARS):
        a1, a2 = AGE1_0 + t, AGE2_0 + t
        rmd = trad / RMD_DIVISOR[min(a1, 90)] if a1 >= RMD_START else 0.0
        div = DIV_YIELD * 500_000  # rough
        if a2 < 65:  # household still on ACA
            convs[t] = max(0.0, FPL400 - 500 - div - rmd)
        else:
            ss = SS_EACH * ((a1 >= 70) + (a2 >= 70))
            convs[t] = max(0.0, 211_400 + STD_DED - rmd - 0.85 * ss)
        convs[t] = min(convs[t], trad)
        trad = max(0.0, trad - convs[t] - rmd) * (1 + R)
    return convs


def optimize(seed_policies, heir_rate=HEIR_RATE, grid_step=5_000,
             max_conv=800_000, sweeps=8):
    """Coordinate ascent over per-year conversions, multi-start + refinement.

    Deterministic problem, so this is a lower bound on the true optimum.
    """
    f = lambda c: simulate(c, heir_rate=heir_rate)
    grid = np.arange(0, max_conv + 1, grid_step, dtype=float)
    best_c, best_v = None, -np.inf
    for seed in seed_policies:
        c = seed.copy()
        v = f(c)
        for _ in range(sweeps):
            improved = False
            for t in range(YEARS):
                orig = c[t]
                vals = []
                for g in grid:
                    c[t] = g
                    vals.append(f(c))
                k = int(np.argmax(vals))
                if vals[k] > v + 1e-6:
                    v, improved = vals[k], True
                    c[t] = grid[k]
                else:
                    c[t] = orig
            if not improved:
                break
        if v > best_v:
            best_v, best_c = v, c.copy()
    # escape moves: zero out each year / shift mass between year pairs
    for _ in range(3):
        improved = False
        for t in range(YEARS):
            if best_c[t] == 0:
                continue
            for u in range(YEARS):
                if u == t:
                    continue
                trial = best_c.copy()
                trial[u] += trial[t]
                trial[t] = 0.0
                v = f(trial)
                if v > best_v + 1e-6:
                    best_v, best_c, improved = v, trial, True
        if not improved:
            break
    # refinement: fine local grid around the incumbent, until converged
    for _ in range(10):
        improved = False
        for t in range(YEARS):
            orig = best_c[t]
            fine = np.unique(np.clip(orig + np.arange(-12_000, 12_001, 500), 0, max_conv))
            for g in fine:
                best_c[t] = g
                v = f(best_c)
                if v > best_v + 1e-6:
                    best_v, orig, improved = v, g, True
            best_c[t] = orig
        if not improved:
            break
    return best_c, best_v


import sys

if __name__ == "__main__":
    policies = {
        "1. no conversion": np.zeros(YEARS),
        "2. fill 12% bracket": policy_fill_bracket(100_800),
        "3. fill 24% bracket": policy_fill_bracket(211_400),
        "4. cliff-aware greedy": policy_cliff_greedy(),
    }
    results = {name: simulate(c) for name, c in policies.items()}
    # bang-bang style seeds: all mass early in k big years
    bb = np.zeros(YEARS)
    bb[:3] = [500_000, 500_000, 500_000]
    bb2 = np.zeros(YEARS)
    bb2[[0, 2, 4]] = [400_000, 500_000, 600_000]
    # lumpy seed: front-loaded big-conversion years + steady mop-up under the
    # SS hump (empirically the basin of the global optimum on this landscape)
    bb3 = np.zeros(YEARS)
    bb3[:5] = [355_000, 65_000, 120_000, 605_000, 305_000]
    for t in range(10, YEARS):
        bb3[t] = 10_000
    seeds = [np.zeros(YEARS), policies["4. cliff-aware greedy"],
             policies["2. fill 12% bracket"], bb, bb2, bb3]
    c_opt, v_opt = optimize(seeds)
    results["5. optimized (coord ascent)"] = v_opt
    policies["5. optimized (coord ascent)"] = c_opt

    base = results["1. no conversion"]
    print(f"{'policy':<30}{'estate@90 (real $)':>20}{'vs no-conv':>14}")
    for name, v in results.items():
        print(f"{name:<30}{v:>20,.0f}{v - base:>+14,.0f}")

    print("\nOptimized conversion path (nonzero years):")
    _, rows = simulate(c_opt, detail=True)
    for r in rows:
        if r["conv"] > 0 or r["subsidy"] > 0 or r["irmaa"] > 0:
            print(f"  {r['year']} (age {r['a1']}): conv={r['conv']:>9,.0f}"
                  f"  MAGI={r['magi']:>9,.0f}  subsidy={r['subsidy']:>7,.0f}"
                  f"  IRMAA={r['irmaa']:>6,.0f}  fed={r['fed']:>8,.0f}")

    print("\nHeir-rate sensitivity (re-optimized per rate):")
    print(f"{'heir rate':<12}{'no-conv':>14}{'greedy':>14}{'optimal':>14}"
          f"{'opt-greedy gap':>16}")
    for hr in (0.0, 0.12, 0.24, 0.32):
        v0 = simulate(np.zeros(YEARS), heir_rate=hr)
        vg = simulate(policies["4. cliff-aware greedy"], heir_rate=hr)
        _, vo = optimize(seeds + [c_opt], heir_rate=hr)
        print(f"{hr:<12.0%}{v0:>14,.0f}{vg:>14,.0f}{vo:>14,.0f}"
              f"{vo - vg:>+16,.0f}")

    if "--wv" in sys.argv:
        # West Virginia sensitivity: highest-premium state, ~$44k/yr for
        # this 60yo couple (KFF; single 60yo at 401% FPL pays ~$22,006/yr).
        WV_PREM = 44_000.0
        print("\n=== West Virginia high-premium sensitivity "
              f"(premium=${WV_PREM:,.0f}) ===")
        wv_no = simulate(np.zeros(YEARS), premium=WV_PREM)
        wv_greedy = simulate(policies["4. cliff-aware greedy"], premium=WV_PREM)
        # re-optimize at WV premium (premium-aware coordinate ascent; optimize()
        # would use the default national premium, so we inline it here):
        f_wv = lambda c: simulate(c, premium=WV_PREM)
        c_wv = c_opt.copy()
        v_wv = f_wv(c_wv)
        grid = np.arange(0, 800_001, 5_000, dtype=float)
        for seed in seeds + [c_opt]:
            c = seed.copy(); v = f_wv(c)
            for _ in range(8):
                improved = False
                for t in range(YEARS):
                    orig = c[t]; vals = []
                    for g in grid:
                        c[t] = g; vals.append(f_wv(c))
                    k = int(np.argmax(vals))
                    if vals[k] > v + 1e-6:
                        v, improved = vals[k], True; c[t] = grid[k]
                    else:
                        c[t] = orig
                if not improved:
                    break
            if v > v_wv:
                v_wv, c_wv = v, c.copy()
        # fine refinement
        for _ in range(10):
            improved = False
            for t in range(YEARS):
                orig = c_wv[t]
                fine = np.unique(np.clip(orig + np.arange(-12_000, 12_001, 500),
                                         0, 800_000))
                for g in fine:
                    c_wv[t] = g; v = f_wv(c_wv)
                    if v > v_wv + 1e-6:
                        v_wv, orig, improved = v, g, True
                c_wv[t] = orig
            if not improved:
                break
        print(f"{'policy':<30}{'estate@90 (real $)':>20}{'vs no-conv':>14}")
        print(f"{'1. no conversion':<30}{wv_no:>20,.0f}{0:>+14,.0f}")
        print(f"{'4. cliff-aware greedy':<30}{wv_greedy:>20,.0f}"
              f"{wv_greedy - wv_no:>+14,.0f}")
        print(f"{'5. optimized (coord ascent)':<30}{v_wv:>20,.0f}"
              f"{v_wv - wv_no:>+14,.0f}")
        print(f"\nWV optimal-vs-greedy gap: {v_wv - wv_greedy:>+,.0f}")
        print("WV optimal conversion path (nonzero years):")
        _, wv_rows = simulate(c_wv, premium=WV_PREM, detail=True)
        for r in wv_rows:
            if r["conv"] > 0:
                print(f"  {r['year']} (age {r['a1']}): conv={r['conv']:>9,.0f}"
                      f"  MAGI={r['magi']:>9,.0f}  subsidy={r['subsidy']:>7,.0f}")

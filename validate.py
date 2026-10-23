"""
Self-checks for le_engine. Run: python validate.py
Every check prints PASS/FAIL with the numbers it compared.
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from le_engine import *

ok_all = True


def check(name, cond, detail):
    global ok_all
    ok_all &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")


# 1. Orifice equation vs published worked example (ResFrac 'A to Z' guide, Fig. 9.10 text):
#    80 bpm, 8 clusters x 4 holes 0.4 in, 10 ppg, Cd 0.85 -> ~800 psi
v = float(perf_dp(80, 32, 0.4, 0.85, 10))
check("perf friction vs ResFrac example", abs(v - 800) < 5, f"{v:.1f} psi (expected ~800)")

# 2. Distribution solver vs brute-force root finding
cl = [Cluster(f"C{i}", 15000 + 40 * i, s, n, 0.40, 0.56, k_nwb=k)
      for i, (s, n, k) in enumerate([(8000, 6, 20), (8150, 6, 40), (7900, 5, 0), (8250, 7, 25), (8050, 6, 10)])]
a = {k: np.array([getattr(c, k) for c in cl], float) for k in
     ("sigma_psi", "n_perf", "d_in", "cd", "k_nwb", "beta_nwb", "k_net", "m_net")}
q, P = solve_distribution(a["sigma_psi"], a["n_perf"], a["d_in"], a["cd"], a["k_nwb"], a["beta_nwb"],
                          a["k_net"], a["m_net"], 90.0, 8.4)
resid = [float(entry_pressure(q[i], c.sigma_psi, c.n_perf, c.d_in, c.cd, c.k_nwb, c.beta_nwb, 0, 0.25, 8.4)) - P
         for i, c in enumerate(cl)]
check("operating point: every cluster at common pressure", max(abs(r) for r in resid) < 0.5,
      f"max |residual| = {max(abs(r) for r in resid):.3f} psi, P = {P:.1f}")
check("operating point: mass balance", abs(q.sum() - 90) < 1e-6, f"sum q = {q.sum():.6f} bpm")

# 3. Perf-only closed form: q_i = A_i*sqrt(P - sigma_i) -> check against solver
cl2 = [Cluster(f"C{i}", 15000 + 40 * i, s, 6, 0.40, 0.56) for i, s in enumerate([8000, 8100, 7950])]
s2 = np.array([c.sigma_psi for c in cl2], float)
q2, P2 = solve_distribution(s2, 6, 0.4, 0.56, 0, 0.5, 0, 0.25, 60.0, 8.4)
A = 6 * 0.4 ** 2 * 0.56 / math.sqrt(K_PERF * 8.4)
from scipy.optimize import brentq
Pcf = brentq(lambda p: (A * np.sqrt(np.maximum(p - s2, 0))).sum() - 60, s2.min(), s2.max() + 1e5)
check("perf-only analytic operating point", abs(Pcf - P2) < 0.05, f"solver {P2:.2f} vs analytic {Pcf:.2f}")

# 4. Step-down recovery on synthetic data with known truth
rng = np.random.default_rng(42)
truth_n, d0, cd0, k_nwb_true = 30, 0.40, 0.56, 30.0
a_true = K_PERF * 8.4 / (truth_n ** 2 * d0 ** 4 * cd0 ** 2)
Q = np.array([90, 70, 50, 30.0])
isip = 8350.0
p = isip + a_true * Q ** 2 + k_nwb_true * Q ** 0.5 + rng.normal(0, 15, 4)
r = fit_step_down(Q, p, 8.4, d0, isip_bh=isip, beta=0.5)
n_est = r.n_open_table[0.56]
check("SDT: open perforations recovered (Cd known)", abs(n_est - truth_n) / truth_n < 0.05,
      f"n_open {n_est:.2f} vs truth {truth_n}; A_eff CI {r.a_eff_ci[0]:.3f}-{r.a_eff_ci[1]:.3f} in^2")
check("SDT: tortuosity recovered", abs(r.b_nwb - k_nwb_true) < 12, f"b = {r.b_nwb:.1f} vs truth {k_nwb_true}")
print("      SDT synthetic pre-proppant steps (for case_example.json):", [[int(x), round(float(y))] for x, y in zip(Q, p)])

# 5. Identifiability demo: same A_eff, different (n, Cd) -> identical SDT
a1 = K_PERF * 8.4 / (30 ** 2 * 0.4 ** 4 * 0.56 ** 2)
a2 = K_PERF * 8.4 / ((30 * 0.56 / 0.85) ** 2 * 0.4 ** 4 * 0.85 ** 2)
check("identifiability: 30 perfs @Cd .56 == 19.8 perfs @Cd .85", abs(a1 - a2) < 1e-12,
      f"a = {a1:.5f} vs {a2:.5f} psi/bpm^2 -> SDT alone cannot separate n and Cd")

# 6. Erosion: two-SDT closed-form alpha recovers the alpha used to generate the data
sched = [(10, 90, 0), (20, 90, 0.5), (20, 90, 1.0), (20, 90, 1.5), (20, 90, 2.0), (20, 90, 2.0), (5, 90, 0)]
ep_true = ErosionParams(alpha=3.1e-13, beta=5e-9, cd_max=0.89)   # fast rounding -> Cd at max early
sim = simulate_lumped(30, 0.40, 0.56, sched, ep_true, dt_min=0.05)
alpha_cf, d_end = alpha_from_two_sdt(30, 0.40, sim["a_eff_end"], sim["Cd_end"], sched)
check("erosion: closed-form alpha from two SDTs", abs(alpha_cf / ep_true.alpha - 1) < 0.05,
      f"alpha {alpha_cf:.3e} vs truth {ep_true.alpha:.3e}; D_end {d_end:.3f} in")

# 7. Global calibration across several synthetic stages (alpha + beta) using end SDT + friction trace
stages = []
ep_gen = ErosionParams(alpha=2.0e-13, beta=2.0e-10, cd_max=0.89)
for n_open, rate in ((30, 90), (26, 85), (33, 95)):
    sc = [(t, rate, p) for t, _, p in sched]
    sm = simulate_lumped(n_open, 0.40, 0.56, sc, ep_gen)
    tr = sm["trace"]
    aeff = n_open * tr[:, 4] * tr[:, 3] ** 2
    stages.append({"schedule": sc, "n_open": n_open, "d0": 0.40, "cd0": 0.56, "a_eff_end": sm["a_eff_end"],
                   "trace_t": tr[::5, 0], "trace_aeff": aeff[::5] * (1 + rng.normal(0, 0.01, len(aeff[::5])))})
ep_fit, _ = calibrate_erosion(stages, ErosionParams(alpha=1e-12, beta=1e-9, cd_max=0.89))
check("erosion: global (alpha, beta) calibration", abs(math.log10(ep_fit.alpha / ep_gen.alpha)) < 0.15
      and abs(math.log10(ep_fit.beta / ep_gen.beta)) < 0.15,
      f"alpha {ep_fit.alpha:.2e} (truth {ep_gen.alpha:.1e}), beta {ep_fit.beta:.2e} (truth {ep_gen.beta:.1e})")

# 8. Isotonic tracker: monotone output
raw = np.array([1.0, 1.2, 1.1, 1.5, 1.4, 1.8])
mono = pava_increasing(raw)
check("isotonic tracker is non-decreasing", np.all(np.diff(mono) >= -1e-12), f"{np.round(mono, 3)}")

print("\nALL PASS" if ok_all else "\nSOME CHECKS FAILED")
sys.exit(0 if ok_all else 1)

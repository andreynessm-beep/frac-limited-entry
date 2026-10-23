"""
Recalculates LE_Calculator.xlsx with the `formulas` package (no Excel needed) and compares
the sheet results with le_engine on the same inputs. Run: python validate_excel.py
"""
import json
import math
import os
import sys

import numpy as np
import formulas

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from le_engine import *

XL = os.path.join(HERE, "LE_Calculator.xlsx")
xl = formulas.ExcelModel().loads(XL).finish()
sol = xl.calculate()

vals = {}
for k, v in sol.items():
    try:
        arr = np.atleast_2d(v.value)
    except Exception:
        continue
    key = k.upper().split("]")[-1].replace("'", "")
    if ":" not in key:
        vals[key] = arr[0, 0]


def g(ref):
    return vals[ref.upper()]


# ---- formula errors (only intentional #N/A chart helpers allowed)
allowed = ("CHARTDATA!", "EROSION!O", "SDT!H", "SDT!Q")
bad, na = {}, 0
for k, v in vals.items():
    s = str(v)
    if s.startswith("#"):
        if s == "#N/A" and k.startswith(allowed):
            na += 1
        else:
            bad.setdefault(s, []).append(k)
ok_all = not bad
print(f"[{'PASS' if not bad else 'FAIL'}] formula errors: {sum(len(x) for x in bad.values())} unexpected, "
      f"{na} intentional #N/A in chart helpers" + (f" -> {[(e, c[:6]) for e, c in bad.items()]}" if bad else ""))


def check(name, xl_v, py_v, tol):
    global ok_all
    xl_v, py_v = float(xl_v), float(py_v)
    rel = abs(xl_v - py_v) / max(abs(py_v), 1e-12)
    ok = rel < tol
    ok_all &= ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: Excel {xl_v:.6g} vs Python {py_v:.6g} (rel diff {rel:.2e})")


case = json.load(open(os.path.join(HERE, "case_example.json"), encoding="utf-8"))
cfg = case_si_to_field(case)
rho = cfg["rho_fluid"]
cl = [Cluster(**c) for c in cfg["clusters"]]
a = {k: np.array([getattr(c, k) for c in cl], float) for k in
     ("sigma_psi", "n_perf", "d_in", "cd", "k_nwb", "beta_nwb", "k_net", "m_net")}

# ---- orifice constant
check("orifice constant (SI sheet vs field engine)", g("INPUTS!B12"),
      K_PERF * BPM_PER_M3MIN ** 2 * 25.4 ** 4 / (KGM3_PER_PPG * PSI_PER_MPA), 1e-6)

# ---- distribution (Start state)
Q = case["schedule"][1][1] * BPM_PER_M3MIN
q, P = solve_distribution(a["sigma_psi"], a["n_perf"], a["d_in"], a["cd"], a["k_nwb"], a["beta_nwb"],
                          a["k_net"], a["m_net"], Q, rho)
check("operating pressure P*, MPa", g("DISTRIBUTION!B9"), P / PSI_PER_MPA, 1e-4)
for j in range(len(cl)):
    check(f"cluster {j+1} rate, m3/min", g(f"DISTRIBUTION!H{4+j}"), q[j] / BPM_PER_M3MIN, 2e-3)
check("uniformity index", g("DISTRIBUTION!B11"), uniformity(q)[0], 2e-3)

# ---- SDT
for key, col in (("steps_pre", "B"), ("steps_post", "K")):
    s = cfg["sdt"]
    Qs = np.array([x[0] for x in s[key]]); Ps = np.array([x[1] for x in s[key]])
    r = fit_step_down(Qs, Ps, rho, s["d_in"], isip_bh=s["isip_bh"], beta=0.5)
    check(f"SDT {key}: a, MPa/(m3/min)^2", g(f"SDT!{col}36"), k_field_to_si(r.a_perf, 2), 1e-6)
    check(f"SDT {key}: b, MPa/(m3/min)^0.5", g(f"SDT!{col}37"), k_field_to_si(r.b_nwb, 0.5), 1e-6)
    check(f"SDT {key}: A_eff, mm2", g(f"SDT!{col}43"), r.a_eff * 645.16, 1e-6)

# ---- erosion (lumped, n = all design shots)
n_tot = sum(c.n_perf for c in cl)
d0 = sum(c.n_perf * c.d_in for c in cl) / n_tot
ep = ErosionParams(**case["erosion"])
sim = simulate_lumped(n_tot, d0, 0.56, [tuple(x) for x in cfg["schedule"]], ep, rho_fluid=rho, dt_min=1.0)
tr = sim["trace"]
check("erosion: dP_perf start, MPa", g("EROSION!B43"), tr[0, 5] / PSI_PER_MPA, 2e-3)
check("erosion: dP_perf end, MPa", g("EROSION!B44"), tr[-1, 5] / PSI_PER_MPA, 2e-3)
check("erosion: D end, mm", g("EROSION!B45"), sim["D_end"] * 25.4, 2e-4)
check("erosion: Cd end", g("EROSION!B46"), sim["Cd_end"], 2e-4)

# ---- alpha calibration
s = cfg["sdt"]
pre = fit_step_down(np.array([x[0] for x in s["steps_pre"]]), np.array([x[1] for x in s["steps_pre"]]), rho, s["d_in"],
                    isip_bh=s["isip_bh"], beta=0.5)
post = fit_step_down(np.array([x[0] for x in s["steps_post"]]), np.array([x[1] for x in s["steps_post"]]), rho, s["d_in"],
                     isip_bh=s["isip_bh"], beta=0.5)
n_open = pre.a_eff / (0.56 * s["d_in"] ** 2)
alpha, _ = alpha_from_two_sdt(n_open, s["d_in"], post.a_eff, ep.cd_max, [tuple(x) for x in cfg["schedule"]])
check("alpha calibrated from two SDTs", g("EROSION!B56"), alpha, 5e-3)

print("\nALL PASS" if ok_all else "\nSOME CHECKS FAILED")
sys.exit(0 if ok_all else 1)

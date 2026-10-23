"""
Limited Entry (LE) engine for Plug & Perf multistage fracturing.

Field units throughout the public API:
    rate q, Q ........ bbl/min (bpm)
    density rho ...... lb/gal (ppg)
    diameter d ....... in
    pressure ......... psi
    proppant conc .... ppa (lb proppant per gal clean fluid)
    time ............. min
Erosion ODEs are integrated in SI internally (Long et al. 2018 form).

Physics
-------
Perforation friction (orifice equation, Crump & Conway 1988 form):
    dP_perf = 0.2369 * rho * q^2 / (n^2 * d^4 * Cd^2)
Near-wellbore tortuosity (step-down convention):
    dP_nwb = k_nwb * q^beta,  beta ~ 0.5
Optional fracture-entry / net-pressure term:
    dP_net = k_net * q^m

Cluster i accepts rate q_i such that
    P_w - dP_wb,i = sigma_i + dP_perf,i(q_i) + dP_nwb,i(q_i) + dP_net,i(q_i)
and sum(q_i) = Q. This is the "common operating point" of the conceptual LE plot.

Erosion (Long, Liu, Xu et al., SPE PO 2018; field form in Materials 2025, 18, 2507):
    dD/dt  = alpha * C * v^2
    dCd/dt = beta  * C * v^2 * (1 - Cd/Cd_max)
    C [kg/m3 slurry], v [m/s] velocity through one perforation.

Identifiability (the key practical point):
    A step-down test only resolves the product  A_eff = n * Cd * d^2.
    n, Cd and d are separated by (a) a pre-proppant SDT where d is the gun's
    entry hole and Cd ~ Cd0, (b) the end-of-stage SDT, and (c) the shape of the
    in-stage friction trace (fast drop = Cd rounding, slow drop = diameter growth).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Sequence

import numpy as np
from scipy.optimize import nnls, least_squares

BPM_TO_M3S = 0.158987294928 / 60.0
IN_TO_M = 0.0254
PPG_TO_KGM3 = 119.826427
PA_PER_PSI = 6894.757293
# Exact orifice constant 8/pi^2 in field units = 0.2376 (classic literature value 0.2369 is the
# same equation rounded, 0.3% lower). Exact value keeps Python and the SI Excel sheet identical.
K_PERF = 8 * PPG_TO_KGM3 * BPM_TO_M3S ** 2 / (math.pi ** 2 * IN_TO_M ** 4 * PA_PER_PSI)
GAL_PER_LB_SAND = 1.0 / (8.345 * 2.65)   # absolute volume of quartz sand


# ---------------------------------------------------------------- basics

def perf_dp(q, n, d, cd, rho):
    """Perforation friction, psi. q = rate through the n holes (bpm)."""
    q, n, d, cd = np.asarray(q, float), np.asarray(n, float), np.asarray(d, float), np.asarray(cd, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(n > 0, K_PERF * rho * q ** 2 / (n ** 2 * d ** 4 * cd ** 2), np.inf)


def slurry_density(rho_fluid, ppa, sg_prop=2.65):
    """Slurry density, ppg."""
    abs_vol = 1.0 / (8.345 * sg_prop)
    return (rho_fluid + ppa) / (1.0 + ppa * abs_vol)


def slurry_conc_kgm3(ppa, sg_prop=2.65):
    """Proppant mass per m3 of slurry."""
    abs_vol = 1.0 / (8.345 * sg_prop)
    return ppa / (1.0 + ppa * abs_vol) * PPG_TO_KGM3


def a_eff_from_coeff(a_perf, rho):
    """Effective hydraulic area n*Cd*d^2 (in^2) from dP_perf = a*Q^2."""
    return math.sqrt(K_PERF * rho / a_perf) if a_perf > 0 else math.inf


# ---------------------------------------------------------------- clusters

@dataclass
class Cluster:
    name: str
    md_ft: float
    sigma_psi: float
    n_perf: int
    d_in: float
    cd: float = 0.56
    k_nwb: float = 0.0       # psi / bpm^beta_nwb
    beta_nwb: float = 0.5
    k_net: float = 0.0       # psi / bpm^m_net
    m_net: float = 0.25


def _cluster_arrays(clusters: Sequence[Cluster]):
    g = lambda attr: np.array([getattr(c, attr) for c in clusters], float)
    return {k: g(k) for k in ("md_ft", "sigma_psi", "n_perf", "d_in", "cd",
                              "k_nwb", "beta_nwb", "k_net", "m_net")}


def entry_pressure(q, sigma, n, d, cd, k_nwb, beta_nwb, k_net, m_net, rho):
    """Pressure required inside the casing for a cluster to accept rate q."""
    q = np.maximum(q, 0.0)
    return sigma + perf_dp(q, n, d, cd, rho) + k_nwb * q ** beta_nwb + k_net * q ** m_net


def solve_distribution(sigma, n, d, cd, k_nwb, beta_nwb, k_net, m_net,
                       Q, rho, dp_wb=None, iters=60):
    """
    Vectorised common-operating-point solver.
    Arrays may be shape (C,) or (R, C) for R stochastic realisations.
    dp_wb: casing friction loss from the heel-most cluster to cluster i (psi).
    Returns (q [same shape], P_heel [shape (R,) or scalar]).
    """
    sigma = np.atleast_2d(sigma).astype(float)
    shape = sigma.shape
    bc = lambda x: np.broadcast_to(np.asarray(x, float), shape)
    n, d, cd, k_nwb, beta_nwb, k_net, m_net = map(bc, (n, d, cd, k_nwb, beta_nwb, k_net, m_net))
    dp_wb = np.zeros(shape) if dp_wb is None else bc(dp_wb)
    active = n > 0

    def q_at(P):
        # inner bisection: q_i such that entry_pressure(q_i) = P - dp_wb_i
        target = P[:, None] - dp_wb
        lo = np.zeros(shape)
        hi = np.full(shape, float(Q))
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            f = entry_pressure(mid, sigma, n, d, cd, k_nwb, beta_nwb, k_net, m_net, rho)
            over = f > target
            hi = np.where(over, mid, hi)
            lo = np.where(over, lo, mid)
        q = 0.5 * (lo + hi)
        return np.where(active & (target > sigma), q, 0.0)

    big = np.where(active, sigma + dp_wb, np.nan)
    p_lo = np.nanmin(big, axis=1)
    p_hi = np.nanmax(np.where(active, entry_pressure(Q, sigma, n, d, cd, k_nwb, beta_nwb,
                                                     k_net, m_net, rho) + dp_wb, np.nan), axis=1)
    for _ in range(iters):
        p_mid = 0.5 * (p_lo + p_hi)
        tot = q_at(p_mid).sum(axis=1)
        over = tot > Q
        p_hi = np.where(over, p_mid, p_hi)
        p_lo = np.where(over, p_lo, p_mid)
    P = 0.5 * (p_lo + p_hi)
    q = q_at(P)
    q *= Q / np.maximum(q.sum(axis=1, keepdims=True), 1e-12)   # remove bisection residue
    if q.shape[0] == 1:
        return q[0], float(P[0])
    return q, P


def wellbore_friction(md_ft, q, grad_psi_ft_at_ref, q_ref, exp=1.8):
    """
    Casing friction loss from heel-most cluster to each cluster (psi).
    Flow in a segment = rate still travelling toward the toe.
    grad_psi_ft_at_ref: friction gradient (psi/ft) at q_ref (bpm), scaled by (q/q_ref)^exp.
    Clusters ordered heel -> toe by md.
    """
    if grad_psi_ft_at_ref <= 0:
        return np.zeros_like(np.asarray(q, float))
    md_ft = np.asarray(md_ft, float)
    q = np.atleast_2d(q)
    order = np.argsort(md_ft)
    md_s = md_ft[order]
    q_s = q[:, order]
    flow_past = q_s[:, ::-1].cumsum(axis=1)[:, ::-1]            # rate entering segment ending at cluster j
    seg = np.diff(np.concatenate([[md_s[0]], md_s]))
    dp_seg = grad_psi_ft_at_ref * (flow_past / q_ref) ** exp * seg
    dp = dp_seg.cumsum(axis=1)
    out = np.empty_like(dp)
    out[:, order] = dp
    return out[0] if out.shape[0] == 1 else out


def uniformity(x):
    """Uniformity index 1 - CoV and cluster efficiency (share of clusters >= 50% of mean)."""
    x = np.asarray(x, float)
    m = x.mean(axis=-1, keepdims=True)
    ui = 1.0 - x.std(axis=-1) / np.maximum(m[..., 0], 1e-12)
    ce = (x >= 0.5 * m).mean(axis=-1)
    return ui, ce


# ---------------------------------------------------------------- stage simulation

@dataclass
class ErosionParams:
    alpha: float = 3.1e-13        # m2*s/kg  (diameter erosion)   — Materials 2025 Table 1, quartz
    beta: float = 1.0e-10         # m*s/kg   (Cd rounding)        — same source, range 5.7e-11..1.7e-10
    cd_max: float = 0.89          # Crump & Conway data via Romero fit: 0.56 -> 0.89


@dataclass
class StageSetup:
    clusters: list
    rho_fluid: float = 8.4
    sg_prop: float = 2.65
    schedule: list = field(default_factory=list)   # [(duration_min, rate_bpm, ppa), ...]
    prop_bias: float = 0.0       # +: toe-ward proppant bias, -: heel-ward. c_i ~ 1 + bias * x_i, x in [-0.5, 0.5]
    wb_grad_psi_ft: float = 0.0  # casing friction gradient at wb_q_ref
    wb_q_ref: float = 90.0
    dt_min: float = 1.0


def _prop_weights(md, bias):
    md = np.asarray(md, float)
    span = md.max() - md.min()
    x = (md - md.min()) / span - 0.5 if span > 0 else np.zeros_like(md)
    return np.clip(1.0 + bias * x, 0.0, None)


def simulate_stage(setup: StageSetup, ep: ErosionParams, sigma_override=None):
    """
    Coupled time-march: distribution -> per-cluster velocity -> erosion -> new distribution.
    Each cluster is represented by its average perforation.
    sigma_override: (R, C) stress realisations for Monte-Carlo (vectorised).
    """
    a = _cluster_arrays(setup.clusters)
    sigma = a["sigma_psi"] if sigma_override is None else np.asarray(sigma_override, float)
    sigma = np.atleast_2d(sigma)
    R, C = sigma.shape
    n = np.broadcast_to(a["n_perf"], (R, C))
    D = np.broadcast_to(a["d_in"], (R, C)).copy()
    Cd = np.broadcast_to(a["cd"], (R, C)).copy()
    w = _prop_weights(a["md_ft"], setup.prop_bias)

    t_series, P_s, q_s, dpp_s, D_s, Cd_s, prop_s = [], [], [], [], [], [], []
    prop_cum = np.zeros((R, C))
    t = 0.0
    q_prev = np.full((R, C), 0.0)
    for dur, rate, ppa in setup.schedule:
        steps = max(1, int(round(dur / setup.dt_min)))
        dt = dur / steps
        for _ in range(steps):
            rho_sl = slurry_density(setup.rho_fluid, ppa, setup.sg_prop)
            dp_wb = wellbore_friction(a["md_ft"], q_prev, setup.wb_grad_psi_ft, setup.wb_q_ref) \
                if setup.wb_grad_psi_ft > 0 and q_prev.sum() > 0 else 0.0
            q, P = solve_distribution(sigma, n, D, Cd, a["k_nwb"], a["beta_nwb"], a["k_net"],
                                      a["m_net"], rate, rho_sl, dp_wb=dp_wb)
            q = np.atleast_2d(q)
            P = np.atleast_1d(P)
            q_prev = q
            # proppant concentration per cluster: bias redistributes, total mass conserved
            ww = np.broadcast_to(w, (R, C))
            norm = (q * ww).sum(axis=1, keepdims=True) / np.maximum(q.sum(axis=1, keepdims=True), 1e-12)
            ppa_i = ppa * ww / np.maximum(norm, 1e-12)
            Ckg = slurry_conc_kgm3(ppa_i, setup.sg_prop)
            with np.errstate(divide="ignore", invalid="ignore"):
                v = np.where(n > 0, (q * BPM_TO_M3S) / (n * math.pi / 4 * (D * IN_TO_M) ** 2), 0.0)
            dts = dt * 60.0
            dD_m = ep.alpha * Ckg * v ** 2 * dts
            dCd = ep.beta * Ckg * v ** 2 * (1 - Cd / ep.cd_max) * dts
            D = D + dD_m / IN_TO_M
            Cd = np.minimum(Cd + dCd, ep.cd_max)
            # proppant mass (lb) into each cluster this step
            clean_rate_gpm = q * 42.0 / (1.0 + ppa_i * (1.0 / (8.345 * setup.sg_prop)))
            prop_cum = prop_cum + clean_rate_gpm * ppa_i * dt
            t += dt
            rho_now = slurry_density(setup.rho_fluid, ppa, setup.sg_prop)
            t_series.append(t)
            P_s.append(P.copy())
            q_s.append(q.copy())
            dpp_s.append(perf_dp(q, n, D, Cd, rho_now))
            D_s.append(D.copy())
            Cd_s.append(Cd.copy())
            prop_s.append(prop_cum.copy())
    return {
        "t": np.array(t_series),
        "P": np.array(P_s)[:, 0] if R == 1 else np.array(P_s),
        "q": np.array(q_s)[:, 0] if R == 1 else np.array(q_s),
        "dp_perf": np.array(dpp_s)[:, 0] if R == 1 else np.array(dpp_s),
        "D": np.array(D_s)[:, 0] if R == 1 else np.array(D_s),
        "Cd": np.array(Cd_s)[:, 0] if R == 1 else np.array(Cd_s),
        "prop_lb": np.array(prop_s)[:, 0] if R == 1 else np.array(prop_s),
    }


def simulate_lumped(n_open, d0, cd0, schedule, ep: ErosionParams, rho_fluid=8.4, sg_prop=2.65, dt_min=1.0):
    """Single equivalent perforation set (all clusters lumped). Returns end A_eff and trace."""
    D, Cd, t = d0, cd0, 0.0
    tr = []
    for dur, rate, ppa in schedule:
        steps = max(1, int(round(dur / dt_min)))
        dt = dur / steps
        for _ in range(steps):
            Ckg = slurry_conc_kgm3(ppa, sg_prop)
            v = rate * BPM_TO_M3S / (n_open * math.pi / 4 * (D * IN_TO_M) ** 2)
            D += ep.alpha * Ckg * v ** 2 * dt * 60 / IN_TO_M
            Cd = min(Cd + ep.beta * Ckg * v ** 2 * (1 - Cd / ep.cd_max) * dt * 60, ep.cd_max)
            t += dt
            rho = slurry_density(rho_fluid, ppa, sg_prop)
            tr.append((t, rate, ppa, D, Cd, float(perf_dp(rate, n_open, D, Cd, rho))))
    return {"a_eff_end": n_open * Cd * D ** 2, "D_end": D, "Cd_end": Cd, "trace": np.array(tr)}


# ---------------------------------------------------------------- step-down / step-rate tests

@dataclass
class SDTResult:
    a_perf: float          # psi/bpm^2
    b_nwb: float           # psi/bpm^beta
    beta: float
    p0: float              # fitted or fixed baseline (psi)
    a_eff: float           # n*Cd*d^2, in^2
    rmse: float
    dp_perf_at_max: float
    dp_nwb_at_max: float
    resolvable: bool
    n_open_table: dict     # Cd -> n_open
    a_eff_ci: tuple        # 90% bootstrap interval

    def to_dict(self):
        return asdict(self)


def fit_step_down(rates, p_bh, rho, d_in, isip_bh=None, beta=None, noise_psi=25.0,
                  cd_grid=(0.56, 0.65, 0.75, 0.85, 0.89), n_boot=400, seed=1):
    """
    Fit p_bh(Q) = p0 + a*Q^2 + b*Q^beta with a, b >= 0.
    If isip_bh is given, p0 is fixed to it (recommended: ISIP after the last step).
    beta=None -> profile beta over 0.25..1.0 and keep the best.
    """
    Q = np.asarray(rates, float)
    y = np.asarray(p_bh, float)

    def solve(bt, Qs, ys):
        cols = [Qs ** 2, Qs ** bt]
        if isip_bh is None:
            cols.append(np.ones_like(Qs))
            A = np.column_stack(cols)
            coef, _ = nnls(A, ys)
            a, b, p0 = coef
        else:
            A = np.column_stack(cols)
            coef, _ = nnls(A, ys - isip_bh)
            (a, b), p0 = coef, isip_bh
        res = ys - (p0 + a * Qs ** 2 + b * Qs ** bt)
        return a, b, p0, float(np.sqrt(np.mean(res ** 2)))

    betas = [beta] if beta is not None else list(np.round(np.arange(0.25, 1.01, 0.05), 2))
    best = min((solve(bt, Q, y) + (bt,) for bt in betas), key=lambda r: r[3])
    a, b, p0, rmse, bt = best
    qmax = Q.max()
    dpp, dpn = a * qmax ** 2, b * qmax ** bt
    aeff = a_eff_from_coeff(a, rho)

    rng = np.random.default_rng(seed)
    boot = []
    resid = y - (p0 + a * Q ** 2 + b * Q ** bt)
    sd = max(np.std(resid), noise_psi)
    for _ in range(n_boot):
        yb = p0 + a * Q ** 2 + b * Q ** bt + rng.normal(0, sd, size=len(Q))
        ab = solve(bt, Q, yb)[0]
        boot.append(a_eff_from_coeff(ab, rho))
    boot = np.array(boot)
    finite = boot[np.isfinite(boot)]
    if len(finite) >= 0.95 * len(boot):
        ci = (float(np.percentile(finite, 5)), float(np.percentile(finite, 95)))
    elif len(finite):
        ci = (float(np.percentile(finite, 5)), math.inf)   # upper bound unresolved: perf friction lost in noise
    else:
        ci = (math.nan, math.inf)
    return SDTResult(a, b, bt, p0, aeff, rmse, dpp, dpn,
                     resolvable=dpp > 4 * sd,
                     n_open_table={cd: aeff / (cd * d_in ** 2) for cd in cd_grid},
                     a_eff_ci=ci)


def surface_to_bh(p_surface, rates, tvd_ft, rho_ppg, k_pipe=0.0, pipe_exp=1.8):
    """BHP = STP + hydrostatic - pipe friction (k_pipe * Q^pipe_exp)."""
    rates = np.asarray(rates, float)
    return np.asarray(p_surface, float) + 0.052 * rho_ppg * tvd_ft - k_pipe * rates ** pipe_exp


# ---------------------------------------------------------------- continuous friction tracking

def pava_increasing(y, w=None):
    """Isotonic (non-decreasing) regression, pool-adjacent-violators."""
    y = np.asarray(y, float)
    w = np.ones_like(y) if w is None else np.asarray(w, float)
    vals, wts, cnt = [], [], []
    for yi, wi in zip(y, w):
        vals.append(yi); wts.append(wi); cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, c2 = vals.pop(), wts.pop(), cnt.pop()
            v1, w1, c1 = vals.pop(), wts.pop(), cnt.pop()
            vals.append((v1 * w1 + v2 * w2) / (w1 + w2)); wts.append(w1 + w2); cnt.append(c1 + c2)
    return np.repeat(vals, cnt)


def track_effective_area(rates, bhtp, p_frac, rho_sl, b_nwb=0.0, beta_nwb=0.5, min_rate=5.0):
    """
    In-stage effective perforation area A_eff(t) = n*Cd*d^2 from treating pressure.
    p_frac: fracture-entry pressure estimate (ISIP of the stage, or a trend).
    Erosion can only increase A_eff, so the raw estimate is projected onto a
    non-decreasing curve (isotonic regression). Drops in the raw signal that the
    projection removes flag bridging / screen-out / net-pressure build-up.
    """
    Q = np.asarray(rates, float)
    dpp = np.asarray(bhtp, float) - np.asarray(p_frac, float) - b_nwb * Q ** beta_nwb
    ok = (Q > min_rate) & (dpp > 0)
    raw = np.full_like(Q, np.nan)
    raw[ok] = np.sqrt(K_PERF * np.asarray(rho_sl, float)[ok] * Q[ok] ** 2 / dpp[ok]) \
        if np.ndim(rho_sl) else np.sqrt(K_PERF * rho_sl * Q[ok] ** 2 / dpp[ok])
    mono = np.full_like(Q, np.nan)
    mono[ok] = pava_increasing(raw[ok])
    return raw, mono


# ---------------------------------------------------------------- erosion calibration

def calibrate_erosion(stages, ep0: ErosionParams, rho_fluid=8.4, sg_prop=2.65, fit_beta=True):
    """
    Global alpha (and beta) across stages.
    stages: list of dicts {schedule, n_open, d0, cd0, a_eff_end, [trace_t, trace_aeff]}
      n_open from the pre-proppant SDT (with cd0, d0), a_eff_end from the end-of-stage SDT.
      Optional trace_* (minutes, in^2) from track_effective_area add shape information,
      which is what separates beta (Cd rounding) from alpha (diameter growth).
    Fits log10 parameters by robust least squares on log(A_eff).
    """
    def resid(x):
        ep = ErosionParams(alpha=10 ** x[0], beta=10 ** x[1] if fit_beta else ep0.beta, cd_max=ep0.cd_max)
        out = []
        for s in stages:
            sim = simulate_lumped(s["n_open"], s["d0"], s["cd0"], s["schedule"], ep, rho_fluid, sg_prop)
            out.append(math.log(sim["a_eff_end"] / s["a_eff_end"]))
            if "trace_t" in s:
                tr = sim["trace"]
                aeff_sim = s["n_open"] * tr[:, 4] * tr[:, 3] ** 2
                aeff_i = np.interp(s["trace_t"], tr[:, 0], aeff_sim)
                out.extend(0.3 * np.log(aeff_i / np.asarray(s["trace_aeff"])))
        return np.array(out)

    x0 = [math.log10(ep0.alpha), math.log10(ep0.beta)]
    if not fit_beta:
        r = least_squares(lambda x: resid([x[0], x0[1]]), [x0[0]], loss="soft_l1")
        return ErosionParams(alpha=10 ** r.x[0], beta=ep0.beta, cd_max=ep0.cd_max), r
    r = least_squares(resid, x0, loss="soft_l1")
    return ErosionParams(alpha=10 ** r.x[0], beta=10 ** r.x[1], cd_max=ep0.cd_max), r


def alpha_from_two_sdt(n_open, d0_in, a_eff_end, cd_end, schedule, sg_prop=2.65):
    """
    Closed form (same as the Excel sheet): with Cd at end ~ Cd_max,
    D_end^5 = D0^5 + 80*alpha/(pi^2 n^2) * integral(C*Q^2 dt)   (SI).
    """
    d_end = math.sqrt(a_eff_end / (n_open * cd_end))
    integ = sum(slurry_conc_kgm3(ppa, sg_prop) * (rate * BPM_TO_M3S) ** 2 * dur * 60
                for dur, rate, ppa in schedule)
    return ((d_end * IN_TO_M) ** 5 - (d0_in * IN_TO_M) ** 5) * math.pi ** 2 * n_open ** 2 / (80 * integ), d_end


# ---------------------------------------------------------------- design screening

def design_screen(base: StageSetup, ep: ErosionParams, designs, sigma_sd_psi=150.0,
                  n_real=200, seed=7, target_ui=0.8):
    """
    Monte-Carlo over cluster stress heterogeneity for candidate designs.
    designs: list of dicts {label, n_perf (int or list), d_in, [rate]}.
    Returns rows with initial/end perf friction and P10/P50 uniformity of proppant per cluster.
    """
    rng = np.random.default_rng(seed)
    base_sig = np.array([c.sigma_psi for c in base.clusters])
    sig = base_sig + rng.normal(0, sigma_sd_psi, size=(n_real, len(base_sig)))
    rows = []
    for dz in designs:
        cl = []
        n_list = dz["n_perf"] if isinstance(dz["n_perf"], (list, tuple)) else [dz["n_perf"]] * len(base.clusters)
        for c, n_i in zip(base.clusters, n_list):
            cc = Cluster(**{**asdict(c), "n_perf": n_i, "d_in": dz["d_in"]})
            cl.append(cc)
        sched = base.schedule if "rate" not in dz else [(t, dz["rate"], p) for t, _, p in base.schedule]
        st = StageSetup(**{**base.__dict__, "clusters": cl, "schedule": sched})
        sim = simulate_stage(st, ep, sigma_override=sig)
        prop_end = sim["prop_lb"][-1]                       # (R, C)
        ui, ce = uniformity(prop_end)
        dpp0 = np.median(sim["dp_perf"][0].mean(axis=1))
        dppE = np.median(sim["dp_perf"][-1].mean(axis=1))
        rows.append({
            "design": dz["label"],
            "total_perfs": int(sum(n_list)),
            "d_in": dz["d_in"],
            "dP_perf_start_psi": round(float(dpp0)),
            "dP_perf_end_psi": round(float(dppE)),
            "friction_retained_%": round(100 * float(dppE / dpp0), 1),
            "UI_P10": round(float(np.percentile(ui, 10)), 3),
            "UI_P50": round(float(np.percentile(ui, 50)), 3),
            "CE_P10": round(float(np.percentile(ce, 10)), 3),
            "P(UI>=target)": round(float((ui >= target_ui).mean()), 3),
        })
    return rows


# ---------------------------------------------------------------- SI interface
# Case files and charts are in SI-practical units; the core stays in field units.
#   pressure MPa | rate m3/min | diameter mm | density kg/m3 | proppant kg per m3 clean fluid
#   MD m | k_nwb MPa/(m3/min)^beta | casing friction gradient MPa/m

PSI_PER_MPA = 145.0377
BPM_PER_M3MIN = 6.289811
KGM3_PER_PPG = PPG_TO_KGM3
FT_PER_M = 1 / 0.3048


def k_si_to_field(k_si, expo):
    """Coefficient of dP = k*q^expo: MPa/(m3/min)^expo -> psi/bpm^expo."""
    return k_si * PSI_PER_MPA / BPM_PER_M3MIN ** expo


def k_field_to_si(k_f, expo):
    return k_f / PSI_PER_MPA * BPM_PER_M3MIN ** expo


def case_si_to_field(cfg):
    """Convert an SI case dict (units == 'SI') to the field-unit dict the engine reads."""
    if cfg.get("units", "field").upper() != "SI":
        return cfg
    out = dict(cfg)
    out["rho_fluid"] = cfg["rho_fluid"] / KGM3_PER_PPG
    out["clusters"] = []
    for c in cfg["clusters"]:
        beta = c.get("beta_nwb", 0.5)
        m = c.get("m_net", 0.25)
        out["clusters"].append({
            "name": c["name"], "md_ft": c["md_m"] * FT_PER_M, "sigma_psi": c["sigma_mpa"] * PSI_PER_MPA,
            "n_perf": c["n_perf"], "d_in": c["d_mm"] / 25.4, "cd": c.get("cd", 0.56),
            "k_nwb": k_si_to_field(c.get("k_nwb", 0.0), beta), "beta_nwb": beta,
            "k_net": k_si_to_field(c.get("k_net", 0.0), m), "m_net": m})
    out["schedule"] = [[t, q * BPM_PER_M3MIN, c / KGM3_PER_PPG] for t, q, c in cfg["schedule"]]
    out["wb_grad_psi_ft"] = cfg.get("wb_grad_mpa_m", 0.0) * PSI_PER_MPA / FT_PER_M
    out["wb_q_ref"] = cfg.get("wb_q_ref_m3min", 14.3) * BPM_PER_M3MIN
    if "sdt" in cfg:
        s = cfg["sdt"]
        out["sdt"] = {"d_in": s["d_mm"] / 25.4, "isip_bh": s["isip_bh_mpa"] * PSI_PER_MPA}
        for key in ("steps_pre", "steps_post"):
            if key in s:
                out["sdt"][key] = [[q * BPM_PER_M3MIN, p * PSI_PER_MPA] for q, p in s[key]]
    if "designs" in cfg:
        out["designs"] = [{**{k: v for k, v in d.items() if k not in ("d_mm", "rate_m3min")},
                           "d_in": d["d_mm"] / 25.4,
                           **({"rate": d["rate_m3min"] * BPM_PER_M3MIN} if "rate_m3min" in d else {})}
                          for d in cfg["designs"]]
    if "monte_carlo" in cfg:
        mc = dict(cfg["monte_carlo"])
        mc["sigma_sd_psi"] = mc.pop("sigma_sd_mpa", 1.0) * PSI_PER_MPA
        out["monte_carlo"] = mc
    return out


def load_case(path):
    with open(path, encoding="utf-8") as f:
        cfg = case_si_to_field(json.load(f))
    clusters = [Cluster(**c) for c in cfg["clusters"]]
    ep = ErosionParams(**cfg.get("erosion", {}))
    st = StageSetup(clusters=clusters, rho_fluid=cfg.get("rho_fluid", 8.4), sg_prop=cfg.get("sg_prop", 2.65),
                    schedule=[tuple(s) for s in cfg["schedule"]], prop_bias=cfg.get("prop_bias", 0.0),
                    wb_grad_psi_ft=cfg.get("wb_grad_psi_ft", 0.0), wb_q_ref=cfg.get("wb_q_ref", 90.0),
                    dt_min=cfg.get("dt_min", 1.0))
    return cfg, st, ep

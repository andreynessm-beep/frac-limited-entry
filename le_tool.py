"""
Command-line runner for le_engine. Charts and summary are in SI
(MPa, m3/min, mm, kg/m3, m); the case file may be SI ("units": "SI") or field.

    python le_tool.py case_example.json            # full run -> out/ folder
    python le_tool.py case_example.json --no-mc     # skip Monte-Carlo design screen

Outputs (out/):
    01_le_operating_point.png   conceptual LE plot at stage start and end (after erosion)
    02_stage_erosion.png        per-cluster perf friction, Cd, diameter, rate share vs time
    03_sdt_fit.png              step-down fits (pre-proppant and end-of-stage)
    04_design_screen.png/.csv   Monte-Carlo comparison of perforation designs
    summary.json                all numbers behind the charts
"""

import argparse
import csv
import json
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from le_engine import (entry_pressure, fit_step_down, simulate_stage, alpha_from_two_sdt, design_screen,
                       load_case, uniformity, slurry_density, solve_distribution, _cluster_arrays,
                       PSI_PER_MPA, BPM_PER_M3MIN, k_field_to_si)

plt.rcParams.update({"font.family": "Arial", "font.size": 9, "axes.grid": True, "grid.alpha": 0.3})

MPA = lambda psi: np.asarray(psi, float) / PSI_PER_MPA
M3MIN = lambda bpm: np.asarray(bpm, float) / BPM_PER_M3MIN
MM = lambda inch: np.asarray(inch, float) * 25.4
LANG = "en"


def t(en, ru):
    return ru if LANG == "ru" else en


def nm(name):
    return name.replace("heel", "пятка").replace("toe", "носок").replace("tapered", "переменный") if LANG == "ru" else name


def le_plot(ax, st, D, Cd, Q, rho, title):
    a = _cluster_arrays(st.clusters)
    q_op, P_op = solve_distribution(a["sigma_psi"], a["n_perf"], D, Cd, a["k_nwb"], a["beta_nwb"],
                                    a["k_net"], a["m_net"], Q, rho)
    qq = np.linspace(0, 2.2 * Q / len(st.clusters), 200)
    for i, c in enumerate(st.clusters):
        p = entry_pressure(qq, c.sigma_psi, c.n_perf, D[i], Cd[i], c.k_nwb, c.beta_nwb, c.k_net, c.m_net, rho)
        ax.plot(M3MIN(qq), MPA(p), lw=1.4, label=nm(c.name))
        ax.plot(M3MIN(q_op[i]), MPA(P_op), "o", ms=5, color=ax.lines[-1].get_color())
    ax.axhline(MPA(P_op), color="k", ls="--", lw=1)
    ax.axvline(M3MIN(Q / len(st.clusters)), color="grey", ls=":", lw=1)
    ax.text(0.01, 0.97, t(f"common operating point P = {MPA(P_op):.2f} MPa\nUI = {uniformity(q_op)[0]:.3f}", f"общая рабочая точка P = {MPA(P_op):.2f} МПа\nUI = {uniformity(q_op)[0]:.3f}"),
            transform=ax.transAxes, va="top", fontsize=8, bbox=dict(boxstyle="round", fc="white", ec="0.8"))
    ax.set_xlabel(t("cluster rate, m³/min (dotted = ideal equal share)", "расход кластера, м³/мин (пунктир — равная доля)"))
    ax.set_ylabel(t("casing pressure at cluster, MPa", "давление в обсадке у кластера, МПа"))
    ax.set_title(title)
    ax.set_ylim(MPA(min(c.sigma_psi for c in st.clusters) - 200), MPA(P_op + 800))
    return q_op, P_op


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-mc", action="store_true")
    ap.add_argument("--lang", default="en", choices=["en", "ru"])
    args = ap.parse_args()
    global LANG
    LANG = args.lang

    cfg, st, ep = load_case(args.case)
    os.makedirs(args.out, exist_ok=True)
    summary = {"units": "MPa, m3/min, mm, kg (proppant), min"}

    sim = simulate_stage(st, ep)
    a = _cluster_arrays(st.clusters)
    names = [c.name for c in st.clusters]
    Q_main = max(r for _, r, _ in st.schedule)
    rho0 = slurry_density(st.rho_fluid, 0.0, st.sg_prop)

    # ---- 01 conceptual LE plot: start vs end of stage
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    q0, P0 = le_plot(axes[0], st, a["d_in"], a["cd"], Q_main, rho0, t("Start of stage (no erosion)", "Начало стадии (без эрозии)"))
    q1, P1 = le_plot(axes[1], st, sim["D"][-1], sim["Cd"][-1], Q_main, rho0, t("End of stage (eroded perforations)", "Конец стадии (размытые перфорации)"))
    axes[0].legend(fontsize=7, loc="lower right")
    fig.suptitle(t("Limited-entry operating point: cluster entry curves vs common casing pressure", "Рабочая точка Limited Entry: кривые входа кластеров и общее давление в обсадке"))
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "01_le_operating_point.png"), dpi=150)
    plt.close(fig)

    # ---- 02 erosion traces
    tt = sim["t"]
    share = sim["q"] / sim["q"].sum(axis=1, keepdims=True)
    fig, ax = plt.subplots(2, 2, figsize=(12, 7), sharex=True)
    for i, n in enumerate(names):
        ax[0, 0].plot(tt, MPA(sim["dp_perf"][:, i]), label=nm(n))
        ax[0, 1].plot(tt, 100 * share[:, i])
        ax[1, 0].plot(tt, sim["Cd"][:, i])
        ax[1, 1].plot(tt, MM(sim["D"][:, i]))
    ax[0, 0].set_ylabel(t("perforation friction, MPa", "трение на перфорациях, МПа")); ax[0, 0].legend(fontsize=7)
    ax[0, 1].set_ylabel(t("rate share, %", "доля расхода, %")); ax[0, 1].axhline(100 / len(names), color="k", ls=":", lw=1)
    ax[1, 0].set_ylabel(t("discharge coefficient Cd", "коэффициент расхода Cd")); ax[1, 1].set_ylabel(t("perforation diameter, mm", "диаметр перфорации, мм"))
    for x in ax[1]:
        x.set_xlabel(t("time, min", "время, мин"))
    fig.suptitle(t("Coupled erosion and flow distribution through the stage", "Связанные эрозия и распределение потока по ходу стадии"))
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "02_stage_erosion.png"), dpi=150)
    plt.close(fig)

    prop_kg = sim["prop_lb"][-1] * 0.45359237
    ui_p, ce_p = uniformity(prop_kg)
    summary["stage"] = {
        "P_start_MPa": round(float(MPA(P0)), 2), "P_end_MPa": round(float(MPA(P1)), 2),
        "dp_perf_start_MPa": [round(float(x), 2) for x in MPA(sim["dp_perf"][0])],
        "dp_perf_end_MPa": [round(float(x), 2) for x in MPA(sim["dp_perf"][-1])],
        "D_end_mm": [round(float(x), 2) for x in MM(sim["D"][-1])],
        "Cd_end": [round(float(x), 3) for x in sim["Cd"][-1]],
        "proppant_kg": dict(zip(names, [round(float(x)) for x in prop_kg])),
        "proppant_UI": round(float(ui_p), 3), "proppant_CE": round(float(ce_p), 3),
        "fluid_UI_start": round(float(uniformity(q0)[0]), 3), "fluid_UI_end": round(float(uniformity(q1)[0]), 3),
    }

    # ---- 03 SDT fits
    if "sdt" in cfg:
        s = cfg["sdt"]
        fig, axs = plt.subplots(1, 2, figsize=(12, 4.2))
        res = {}
        for ax, key in ((axs[0], "steps_pre"), (axs[1], "steps_post")):
            if key not in s:
                continue
            Q = np.array([x[0] for x in s[key]], float)
            P = np.array([x[1] for x in s[key]], float)
            r = fit_step_down(Q, P, rho0, s["d_in"], isip_bh=s.get("isip_bh"), beta=0.5)
            res[key] = r
            qq = np.linspace(0, Q.max() * 1.05, 100)
            ax.plot(M3MIN(Q), MPA(P - r.p0), "ko", label=t("measured BHP − ISIP", "замер BHP − ISIP"))
            ax.plot(M3MIN(qq), MPA(r.a_perf * qq ** 2 + r.b_nwb * qq ** r.beta), "k-", label=t("total fit", "подгонка, всего"))
            ax.plot(M3MIN(qq), MPA(r.a_perf * qq ** 2), "C0--", label=t("perforations  a·Q²", "перфорации  a·Q²"))
            ax.plot(M3MIN(qq), MPA(r.b_nwb * qq ** r.beta), "C3--", label=t(f"near-wellbore  b·Q^{r.beta:.2f}", f"призабойная зона  b·Q^{r.beta:.2f}"))
            n_txt = ", ".join(f"Cd {cd:.2f}: {n:.1f}" for cd, n in r.n_open_table.items())
            aeff_mm2 = r.a_eff * 645.16
            ci = [x * 645.16 if math.isfinite(x) else float("inf") for x in r.a_eff_ci]
            ttl = "тест до проппанта" if key == "steps_pre" else "тест в конце стадии"
            ax.set_title(t(f"{key}: A_eff = n·Cd·d² = {aeff_mm2:,.0f} mm²  (90% CI {ci[0]:,.0f}–{ci[1]:,.0f})", f"{ttl}: A_eff = n·Cd·d² = {aeff_mm2:,.0f} мм²  (90% ДИ {ci[0]:,.0f}–{ci[1]:,.0f})"),
                         fontsize=8.5)
            ax.text(0.01, 0.97, t(f"open perfs by assumed Cd → {n_txt}\nresolvable: {r.resolvable}", f"открыто при принятом Cd → {n_txt}\nтест разрешим: {'да' if r.resolvable else 'нет'}"),
                    transform=ax.transAxes, va="top", fontsize=7.5, bbox=dict(boxstyle="round", fc="white", ec="0.8"))
            ax.set_xlabel(t("rate, m³/min", "расход, м³/мин")); ax.set_ylabel(t("pressure loss, MPa", "потери давления, МПа")); ax.legend(fontsize=7, loc="lower right")
            summary.setdefault("sdt", {})[key] = {
                "a_perf_MPa_per_(m3/min)^2": k_field_to_si(r.a_perf, 2),
                "b_nwb_MPa_per_(m3/min)^0.5": k_field_to_si(r.b_nwb, r.beta),
                "A_eff_mm2": round(aeff_mm2, 1), "A_eff_CI90_mm2": [round(x, 1) for x in ci],
                "dP_perf_at_max_MPa": round(float(MPA(r.dp_perf_at_max)), 2),
                "dP_nwb_at_max_MPa": round(float(MPA(r.dp_nwb_at_max)), 2),
                "rmse_MPa": round(float(MPA(r.rmse)), 3), "resolvable": bool(r.resolvable),
                "n_open_by_Cd": {str(k): round(v, 1) for k, v in r.n_open_table.items()}}
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "03_sdt_fit.png"), dpi=150)
        plt.close(fig)

        if "steps_pre" in res and "steps_post" in res:
            cd0 = st.clusters[0].cd
            n_open = res["steps_pre"].a_eff / (cd0 * s["d_in"] ** 2)
            alpha, d_end = alpha_from_two_sdt(n_open, s["d_in"], res["steps_post"].a_eff, ep.cd_max, st.schedule, st.sg_prop)
            summary["erosion_calibration"] = {
                "n_open_from_pre_sdt(Cd0)": round(n_open, 1),
                "D_end_mm(Cd_end=Cd_max)": round(float(MM(d_end)), 2),
                "alpha_calibrated_m2s_per_kg": alpha,
                "note": "two SDTs constrain only A_eff; beta (Cd rounding) needs the in-stage friction trace"}

    # ---- 04 Monte-Carlo design screen
    if not args.no_mc and "designs" in cfg:
        mc = cfg.get("monte_carlo", {})
        rows = design_screen(st, ep, cfg["designs"], sigma_sd_psi=mc.get("sigma_sd_psi", 150),
                             n_real=mc.get("n_real", 200), target_ui=mc.get("target_ui", 0.8))
        for r in rows:
            r["d_mm"] = round(float(MM(r.pop("d_in"))), 1)
            r["dP_perf_start_MPa"] = round(float(MPA(r.pop("dP_perf_start_psi"))), 2)
            r["dP_perf_end_MPa"] = round(float(MPA(r.pop("dP_perf_end_psi"))), 2)
        with open(os.path.join(args.out, "04_design_screen.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        fig, ax = plt.subplots(figsize=(9, 4.5))
        for r in rows:
            ax.scatter(r["dP_perf_end_MPa"], r["UI_P10"], s=60)
            ax.annotate(nm(r["design"]), (r["dP_perf_end_MPa"], r["UI_P10"]), fontsize=8, xytext=(5, 3),
                        textcoords="offset points")
        ax.set_xlabel(t("perforation friction at END of stage (after erosion), MPa", "трение на перфорациях в КОНЦЕ стадии (после эрозии), МПа"))
        ax.set_ylabel(t("P10 proppant uniformity index (1 − CoV)", "P10 индекса равномерности по проппанту (1 − CoV)"))
        ax.set_title(t(f"Design screen: {mc.get('n_real', 200)} stress realisations, sd = {MPA(mc.get('sigma_sd_psi', 150)):.1f} MPa", f"Сравнение дизайнов: {mc.get('n_real', 200)} реализаций напряжений, σ = {MPA(mc.get('sigma_sd_psi', 150)):.1f} МПа"))
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "04_design_screen.png"), dpi=150)
        plt.close(fig)
        summary["design_screen"] = rows

    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=float, ensure_ascii=False)
    print(json.dumps(summary, indent=2, default=float, ensure_ascii=False))


if __name__ == "__main__":
    main()

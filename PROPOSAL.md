# Limited Entry Calculator for Plug & Perf: proposal

This answers a request for a practical tool to calculate Limited Entry parameters and calibrate them on SRT/SDT data. The tool comes in two parts:

- **an Excel calculator** that you can use straight away, with live formulas and no macros;
- **a Python engine** for the parts Excel cannot do well.

Both work in SI units: MPa, m³/min, mm, kg/m³ and m. The Python engine reads field units as well.

All numbers in the files come from a **synthetic demo case**. They are there to show the workflow, not to describe a real well.

## What it covers 

| Requirement | Where | How |
|---|---|---|
| Perforation friction ΔP_perf (number of shots, diameter, Cd) | Excel → `Design`, `Inputs` | Orifice equation ΔP = 8ρq²/(π²n²d⁴Cd²). It also builds a friction matrix (shots × entry hole) and back-calculates the shots needed for a target ΔP. |
| Perforation erosion (Cd and diameter changing over time) | Excel → `Erosion`; Python → `simulate_stage` | Long et al. (2018) model with two mechanisms: Cd rounding (fast) and diameter growth (slow). It is time-marched through your pump schedule. |
| Calibration against real SRT/SDT | Excel → `SDT`, `Erosion`; Python → `fit_step_down`, `calibrate_erosion` | Fits BHP − ISIP = a·Q² + b·Q^β with a, b ≥ 0. Then calibrates the erosion coefficient from the pre-proppant and end-of-stage tests. |
| Flow distribution and the common operating point (conceptual LE plot) | Excel → `Distribution` with chart; Python → `01_le_operating_point.png` | Every cluster sees the same casing pressure and Σq = Q. The cluster entry curves σᵢ + ΔP_perf + ΔP_nwb are plotted against the common pressure line, at stage start and at stage end. |

## Five things that go beyond a standard LE spreadsheet

1. **The identifiability trap is handled explicitly.** A step-down test only resolves the product **n·Cd·d²**. For example, 30 holes at Cd 0.56 and 19.8 holes at Cd 0.85 give an identical test. So the tool never reports "N perfs open" on its own. It reports A_eff together with an N(Cd) table, and calibrates in this order:
   - the pre-proppant SDT fixes n;
   - the end-of-stage SDT measures erosion;
   - the shape of the in-stage friction trace separates Cd rounding from diameter growth.
2. **Erosion is coupled to the distribution.** A cluster that takes more slurry erodes faster, which lowers its friction, so it takes even more. Lumped "average perforation" models miss this runaway. In the demo, fluid uniformity drops from **0.95 to 0.81** within one stage, even with identical perforations.
3. **Resolvability check.** After heavy erosion, the friction left at the end of the stage can sink into pressure noise. Each SDT gets a "resolvable" flag and a 90% bootstrap interval, so you know when the test cannot be trusted.
4. **Monte Carlo design screen.** Cluster stress is uncertain. For each perforation design, the tool reports:
   - perf friction at the start and at the end of the stage;
   - how much friction is retained (in %);
   - the P10 proppant uniformity;
   - the probability of reaching the target uniformity.

   In the demo, XLE (4 shots per cluster) starts at ~23 MPa but keeps only ~22% of it after erosion. That is still more absolute friction than 6 shots per cluster.
5. **In-stage friction tracker** (Python). It estimates A_eff(t) from treating pressure and projects it onto a non-decreasing curve with isotonic regression, because erosion only enlarges holes. A drop in the raw signal therefore points to bridging, a screen-out or rising net pressure, not to the perforations.

Further options that can be added:

- heel/toe proppant bias, calibrated from DAS data or perforation imaging;
- casing friction between clusters;
- tapered shot counts;
- hierarchical Bayesian calibration of erosion across all stages of a well.

## Data needed to calibrate a real well

For 3–10 stages:

1. **Pre-proppant step-down test:** rate and BHP at each step (or surface pressure plus TVD and pipe-friction data), and the ISIP.
2. **End-of-stage step-down test**, in the same format.
3. **Pump schedule:** duration, rate and proppant concentration per step.
4. **Treating pressure trace** for the stage (optional, but it is what separates Cd rounding from diameter growth).
5. **Per stage:** gun entry-hole diameter, shots per cluster, cluster MD, and stress per cluster or a stress-uncertainty estimate.
6. **Optional:** DAS data, perforation imaging or erosion measurements, to calibrate the heel/toe bias.

## Files

| File | Purpose |
|---|---|
| `LE_Calculator.xlsx` | The Excel tool. Workflow: Inputs → SDT → Erosion → Distribution → Design. The README sheet lists the equations, sources and limitations. |
| `le_engine.py`, `le_tool.py`, `case_example.json` | The Python engine. Run `python le_tool.py case_example.json` to get four charts plus `summary.json` and a CSV. |
| `validate.py`, `validate_excel.py` | Self-checks: 8 physics tests, plus a full recalculation of the workbook cross-checked against the Python engine. Both currently report ALL PASS. |

## Validation done

- **Orifice equation:** it reproduces the published ResFrac example (80 bpm, 32 × 0.4 in, 10 ppg, Cd 0.85) at **803 psi**, against ~800 psi in the guide. The 0.3% gap exists because the classic 0.2369 constant is a rounded value.
- **Synthetic SDT** with known truth: open perforations recovered as 30.2 vs 30, and tortuosity within 10%.
- **Erosion calibration** on synthetic stages: α and β recovered to within 1%.
- **Excel vs Python:** operating pressure, cluster rates, SDT coefficients, erosion end state and the calibrated α agree to better than 0.01%.

## Sources

- ResFrac, *The A to Z Guide to Accelerating Continuous Improvement with ResFrac*, arXiv:2205.14820.
- Long, Liu, Xu et al., *A Perforation-Erosion Model for Hydraulic-Fracturing Applications*, SPE Production & Operations 33(4), 2018. Long & Xu, SPE Journal 2017 (SPE-185173-PA).
- *Construction and Application of a Quantitative Perforation Erosion Model Based on Field Experiments*, Materials 2025, 18, 2507: field values of α and β, and the two-stage erosion behaviour.
- Crump & Conway (SPE 15474) data on Cd going from 0.56 to 0.89, via the Romero (2000) fit. Cited in Processes 2022, 10, 1058.
- Mondal et al., *Advancements in Step Down Tests…*, SPE-204147-MS, 2021.
- Merry & Dalamarinis, tube-wave perforation assessment, SPE-201686-MS, 2020.
- PEER consortium (NSI), OGJ 2022: a 17% increase in perforation diameter causes a loss of more than 62% of the limited-entry pressure.

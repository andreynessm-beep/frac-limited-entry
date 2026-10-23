"""
Builds the SI calculator workbook — live formulas only (no macros, no hardcoded results).
    python build_excel.py        -> LE_Calculator.xlsx     (English)
    python build_excel.py ru     -> LE_Calculator_RU.xlsx  (Russian: sheets, labels, switches)
Units: MPa, m3/min, mm, kg/m3, m, min. Proppant concentration = kg per m3 of clean fluid.
"""
import json
import os
import sys

from openpyxl import Workbook
from openpyxl.chart import ScatterChart, Reference, Series
from openpyxl.comments import Comment
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.datavalidation import DataValidation

HERE = os.path.dirname(os.path.abspath(__file__))
RU = len(sys.argv) > 1 and sys.argv[1].lower() == "ru"
OUT = os.path.join(HERE, "LE_Calculator_RU.xlsx" if RU else "LE_Calculator.xlsx")
if RU:
    import xl_ru


def sn(name):
    """Sheet title for the current language."""
    return xl_ru.SHEETS[name] if RU else name


def T(v):
    """Translate a label, or rewrite sheet names / quoted literals inside a formula."""
    if not RU or not isinstance(v, str):
        return v
    if v.startswith("=") or v.startswith('"'):
        for en, ru in xl_ru.SHEETS.items():
            if en != ru:
                v = v.replace(en + "!", "'" + ru + "'!")
        for en, ru in xl_ru.LITERALS:
            v = v.replace(en, ru)
        return v
    return xl_ru.TR.get(v, v)

case = json.load(open(os.path.join(HERE, "case_example.json"), encoding="utf-8"))

NC = 12                      # max clusters
CL0 = 23                     # first cluster row on Inputs
NG = 201                     # grid points (0..200)
NT = 360                     # erosion time rows

F = "Arial"
f_norm = Font(name=F, size=10)
f_bold = Font(name=F, size=10, bold=True)
f_title = Font(name=F, size=14, bold=True)
f_in = Font(name=F, size=10, color="0000FF")
f_note = Font(name=F, size=9, italic=True, color="666666")
fill_in = PatternFill("solid", fgColor="FFF2CC")
fill_key = PatternFill("solid", fgColor="FFFF00")
fill_hdr = PatternFill("solid", fgColor="D9E1F2")
fill_res = PatternFill("solid", fgColor="E2EFDA")
fill_help = PatternFill("solid", fgColor="F2F2F2")
thin = Border(*(Side(style="thin", color="BFBFBF"),) * 4)

wb = Workbook()


def put(ws, ref, val, font=None, fill=None, fmt=None, comment=None):
    c = ws[ref]
    c.value = T(val)
    c.font = font or f_norm
    if fill:
        c.fill = fill
    if fmt:
        c.number_format = fmt
    if comment:
        c.comment = Comment(T(comment), "LE calculator")
    return c


def inp(ws, ref, val, fmt=None, comment=None, key=False):
    c = put(ws, ref, val, f_in, fill_key if key else fill_in, fmt, comment)
    c.border = thin
    return c


def hdr(ws, row, col, labels, width=None):
    for i, t in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=T(t))
        c.font = f_bold
        c.fill = fill_hdr
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = thin


def fcell(ws, r, c, formula, fmt=None, fill=None, font=None):
    cell = ws.cell(row=r, column=c, value=T(formula))
    cell.font = font or f_norm
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    return cell


# =====================================================================  README
ws = wb.active
ws.title = sn("README")
ws.column_dimensions["A"].width = 120
lines = [
    ("Limited Entry Calculator — Plug & Perf (SI units)", f_title),
    ("", None),
    ("WHAT IT DOES", f_bold),
    ("1. Inputs — stage rate, fluid, proppant, up to 12 clusters (stress, shots, entry-hole diameter, Cd, near-wellbore coefficient).", None),
    ("2. SDT — fits step-down / step-rate tests: BHP − ISIP = a·Q² + b·Q^β. Gives effective perforation area A_eff = n·Cd·d² and open perforations for any assumed Cd.", None),
    ("3. Erosion — time-marches the pump schedule with the Long et al. erosion model (Cd rounding + diameter growth); calibrates α from pre- and post-stage SDTs.", None),
    ("4. Distribution — solves the common operating point: every cluster sees the same casing pressure, Σq = Q. Conceptual LE plot included.", None),
    ("5. Design — perforation friction matrix (shots × diameter), required shots for a target friction, start vs end-of-stage friction.", None),
    ("", None),
    ("HOW TO USE", f_bold),
    ("Edit only cells with BLUE text on a light-yellow fill (bright yellow = key switches). Everything else is a formula. Example values are a SYNTHETIC demo case — replace them.", None),
    ("Workflow: Inputs → SDT (pre-proppant test, end-of-stage test) → Erosion (set 'Use calibrated α' = Yes once both SDTs are entered) → Distribution (State = Start / End) → Design.", None),
    ("Clusters: set shots = 0 to switch a cluster off. Rate in m³/min, pressure in MPa, diameter in mm, proppant in kg per m³ of clean fluid.", None),
    ("", None),
    ("EQUATIONS (SI)", f_bold),
    ("Perforation friction:  ΔP_perf [Pa] = 8·ρ·q² / (π²·n²·d⁴·Cd²),  q [m³/s], d [m]  →  in sheet units ΔP [MPa] = K·ρ·q²/(n²·d⁴·Cd²),  K = 8·10⁶/(π²·3600) = 225.16  (q m³/min, d mm).", None),
    ("  The classic field constant 0.2369 (psi, bpm, ppg, in) is the same equation rounded; exact conversion gives 0.2376 (0.3% difference).", None),
    ("Near-wellbore (tortuosity):  ΔP_nwb = k·q^β, β ≈ 0.5.  Cluster entry pressure:  P = σ + ΔP_perf + ΔP_nwb.", None),
    ("Erosion (Long et al. 2018):  dD/dt = α·C·v²,   dCd/dt = β·C·v²·(1 − Cd/Cd_max),   C = proppant kg per m³ slurry, v = velocity in one perforation (m/s).", None),
    ("Two-SDT calibration:  D_end⁵ = D₀⁵ + 80·α/(π²·n²) · ∫C·Q² dt   (SI, Cd at end ≈ Cd_max).", None),
    ("", None),
    ("KEY LIMITATION — IDENTIFIABILITY", f_bold),
    ("A step-down test resolves only the product n·Cd·d². 30 holes at Cd 0.56 and 19.8 holes at Cd 0.85 give an identical test. Use the pre-proppant SDT (d = gun entry hole, Cd ≈ Cd0)", None),
    ("to fix n; the end-of-stage SDT then measures erosion (Cd·d² growth). Separating Cd-rounding (β) from diameter growth (α) needs the in-stage friction trace — see Python tool.", None),
    ("After heavy erosion the end-of-stage perforation friction may be too small vs pressure noise: check 'resolvable' on the SDT sheet before trusting the result.", None),
    ("", None),
    ("WHAT THIS SHEET SIMPLIFIES (the Python tool le_engine.py does it fully)", f_bold),
    ("• Erosion here is lumped (one equivalent perforation set). Python couples erosion per cluster with the distribution solver — the cluster that takes more slurry erodes faster and takes even more (runaway).", None),
    ("• Heel/toe proppant bias, casing friction between clusters, Monte-Carlo over stress uncertainty, multi-stage (α, β) calibration and isotonic in-stage friction tracking are Python-only.", None),
    ("", None),
    ("SOURCES", f_bold),
    ("Orifice equation & worked example: McClure et al., 'The A to Z Guide to Accelerating Continuous Improvement with ResFrac', arXiv:2205.14820 (80 bpm, 32 × 0.4 in, 10 ppg, Cd 0.85 → ~800 psi).", None),
    ("Cd 0.56 → 0.89 after ~2000 lbm proppant per perforation: Crump & Conway (SPE 15474, 1988) data as fitted by Romero (2000), cited in Processes 2022, 10, 1058.", None),
    ("Erosion model: Long, Liu, Xu et al., 'A Perforation-Erosion Model for Hydraulic-Fracturing Applications', SPE Prod & Oper 33(4), 2018; Long & Xu, SPE J. 2017 (SPE-185173-PA).", None),
    ("Default α = 3.1e-13 m²·s/kg and β = 1.0e-10 m·s/kg: lab/field-scale range in 'Construction and Application of a Quantitative Perforation Erosion Model Based on Field Experiments', Materials 2025, 18, 2507 (α 1.0e-13…4.5e-13, β 5.7e-11…9.5e-10). CALIBRATE on your SDTs.", None),
    ("Step-down tests: Mondal et al., 'Advancements in Step Down Tests to Guide Perforation Cluster Design and Limited Entry Pressure Intensities', SPE-204147-MS, 2021.", None),
]
if RU:
    lines = [(t, {"title": f_title, "bold": f_bold}.get(k)) for t, k in xl_ru.README]
for i, (t, fnt) in enumerate(lines, start=1):
    c = ws.cell(row=i, column=1, value=t)
    c.font = fnt or f_norm
    c.alignment = Alignment(wrap_text=True, vertical="top")

# =====================================================================  Inputs
wi = wb.create_sheet(sn("Inputs"))
wi.column_dimensions["A"].width = 40
for col, w in zip("BCDEFGHIJK", (14, 12, 10, 11, 9, 16, 12, 12, 12, 14)):
    wi.column_dimensions[col].width = w
put(wi, "A1", "Inputs (SI)", f_title)
put(wi, "A2", "Blue text on yellow = edit. Grey = helper formulas. Demo values are SYNTHETIC.", f_note)
put(wi, "A3", "Global", f_bold)
g = [
    (4, "Stage rate Q, m³/min", case["schedule"][1][1], "0.00", None),
    (5, "Clean fluid density ρ, kg/m³", case["rho_fluid"], "0", None),
    (6, "Proppant specific gravity", case["sg_prop"], "0.00", None),
    (7, "Initial discharge coefficient Cd0", 0.56, "0.00", "Sharp-edged new perforation. 0.56 from Crump & Conway data (Romero 2000 fit)."),
    (8, "Max discharge coefficient Cd_max", case["erosion"]["cd_max"], "0.00", "Fully rounded entrance. 0.89 from Crump & Conway data (Romero 2000 fit)."),
    (9, "Erosion α input, m²·s/kg (diameter)", case["erosion"]["alpha"], "0.00E+00", "Materials 2025, 18, 2507 Table 1, quartz sand. Replace with calibrated value."),
    (10, "Erosion β, m·s/kg (Cd rounding)", case["erosion"]["beta"], "0.00E+00", "Materials 2025 range 5.7e-11…1.7e-10 (lab), 9.5e-10 (Mahu field)."),
    (11, "Near-wellbore exponent β_nwb", 0.5, "0.00", "Step-down convention: tortuosity ~ Q^0.5."),
]
for r, lab, v, fmt, com in g:
    put(wi, f"A{r}", lab)
    inp(wi, f"B{r}", v, fmt, com)
put(wi, "A12", "Orifice constant K (MPa, m³/min, mm, kg/m³)")
fcell(wi, 12, 2, "=8000000/(PI()^2*3600)", "0.000")
put(wi, "A13", "Use calibrated α from SDTs? (Yes/No)")
inp(wi, "B13", "No", key=True)
put(wi, "A14", "α used in model, m²·s/kg")
fcell(wi, 14, 2, '=IF(B13="Yes",Erosion!B56,B9)', "0.00E+00")
put(wi, "A15", "Distribution state (Start/End of stage)")
inp(wi, "B15", "Start", key=True)
put(wi, "A16", "Open perforations for erosion (Design/SDT)")
inp(wi, "B16", "Design", key=True)
put(wi, "A17", "Erosion time step, min")
inp(wi, "B17", 1, "0.0")
put(wi, "A18", "Active clusters")
fcell(wi, 18, 2, f"=COUNTIF(D{CL0}:D{CL0+NC-1},\">0\")", "0")
put(wi, "A19", "Total perforations in stage")
fcell(wi, 19, 2, f"=SUMIF(D{CL0}:D{CL0+NC-1},\">0\")", "0")
put(wi, "A20", "Equal share per cluster, m³/min")
fcell(wi, 20, 2, "=B4/B18", "0.000")
put(wi, "A21", "Shot-weighted entry hole d0, mm")
fcell(wi, 21, 2, f"=SUMPRODUCT(D{CL0}:D{CL0+NC-1},E{CL0}:E{CL0+NC-1})/B19", "0.00")

for ref in ("B13",):
    dv = DataValidation(type="list", formula1=T('"Yes,No"'), allow_blank=False); wi.add_data_validation(dv); dv.add(ref)
dv = DataValidation(type="list", formula1=T('"Start,End"')); wi.add_data_validation(dv); dv.add("B15")
dv = DataValidation(type="list", formula1=T('"Design,SDT"')); wi.add_data_validation(dv); dv.add("B16")

hdr(wi, CL0 - 1, 1, ["Cluster", "MD, m", "Stress σ, MPa", "Shots n", "Entry hole d, mm", "Cd",
                     "k_nwb, MPa/(m³/min)^β", "d used, mm", "Cd used", "σ (active, min)", "P at equal share, MPa", "σ (active, max)"])
wi.row_dimensions[CL0 - 1].height = 42
for j in range(NC):
    r = CL0 + j
    c = case["clusters"][j] if j < len(case["clusters"]) else None
    inp(wi, f"A{r}", c["name"] if c else f"C{j+1}")
    inp(wi, f"B{r}", c["md_m"] if c else None, "0")
    inp(wi, f"C{r}", c["sigma_mpa"] if c else None, "0.00")
    inp(wi, f"D{r}", c["n_perf"] if c else 0, "0")
    inp(wi, f"E{r}", c["d_mm"] if c else 10.2, "0.00")
    inp(wi, f"F{r}", c["cd"] if c else 0.56, "0.00")
    inp(wi, f"G{r}", c["k_nwb"] if c else 0, "0.000")
    fcell(wi, r, 8, f'=IF($B$15="End",E{r}*Erosion!$B$45/$B$21,E{r})', "0.00", fill_help)
    fcell(wi, r, 9, f'=IF($B$15="End",Erosion!$B$46,F{r})', "0.000", fill_help)
    fcell(wi, r, 10, f"=IF(D{r}>0,C{r},1000000000)", "0.00", fill_help)
    fcell(wi, r, 11, f"=IF(D{r}>0,C{r}+$B$12*$B$5*$B$20^2/(D{r}^2*H{r}^4*I{r}^2)+G{r}*$B$20^$B$11,-1000000000)",
          "0.00", fill_help)
    fcell(wi, r, 12, f"=IF(D{r}>0,C{r},-1000000000)", "0.00", fill_help)
put(wi, f"A{CL0+NC+1}", "End state uses the lumped erosion result (Erosion sheet) for every cluster; per-cluster coupled erosion is in le_engine.py.", f_note)

# =====================================================================  LE_Curves
wc = wb.create_sheet(sn("LE_Curves"))
put(wc, "A1", "Cluster entry curves P_i(q) = σ_i + ΔP_perf(q) + ΔP_nwb(q)  — solver lookup tables, MPa", f_bold)
put(wc, "A2", "q grid spans 0…Q so any split can be represented. Inactive clusters return 0 and are skipped by the solver.", f_note)
hdr(wc, 4, 1, ["k", "q, m³/min"] + [f"=Inputs!A{CL0+j}" for j in range(NC)])
for k in range(NG):
    r = 5 + k
    fcell(wc, r, 1, k)
    fcell(wc, r, 2, f"=Inputs!$B$4*A{r}/{NG-1}", "0.0000")
    for j in range(NC):
        ir = CL0 + j
        fcell(wc, r, 3 + j,
              f"=IF(Inputs!$D${ir}=0,0,Inputs!$C${ir}+Inputs!$B$12*Inputs!$B$5*$B{r}^2/"
              f"(Inputs!$D${ir}^2*Inputs!$H${ir}^4*Inputs!$I${ir}^2)+Inputs!$G${ir}*$B{r}^Inputs!$B$11)", "0.000")
QC = f"LE_Curves!$B$5:$B${4+NG}"


def q_of_p(pref, j):
    """Interpolated inverse of cluster j entry curve at pressure cell pref."""
    ir = CL0 + j
    col = L(3 + j)
    pc = f"LE_Curves!${col}$5:${col}${4+NG}"
    m = f"MATCH({pref},{pc},1)"
    return (f"=IF(Inputs!$D${ir}=0,0,IF({pref}<=Inputs!$C${ir},0,IF({pref}>=INDEX({pc},{NG}),INDEX({QC},{NG}),"
            f"INDEX({QC},{m})+({pref}-INDEX({pc},{m}))*(INDEX({QC},{m}+1)-INDEX({QC},{m}))/"
            f"(INDEX({pc},{m}+1)-INDEX({pc},{m})))))")


# =====================================================================  Distribution
wd = wb.create_sheet(sn("Distribution"))
put(wd, "A1", "Flow distribution — common operating point (Σq = Q, equal casing pressure)", f_title)
wd.column_dimensions["A"].width = 30
wd.column_dimensions["B"].width = 13
G0 = 31     # grid first data row
GE = G0 + NG - 1
labels = [("Min active stress, MPa", "=MIN(Inputs!J23:J34)", "0.000"),
          ("Max P at equal share, MPa", "=MAX(Inputs!K23:K34)", "0.000"),
          ("Coarse bracket index", f"=MIN(MATCH(Inputs!B4,O{G0}:O{GE},1),{NG-1})", "0"),
          ("Bracket P low, MPa", f"=INDEX(B{G0}:B{GE},B5)", "0.000"),
          ("Bracket P high, MPa", f"=INDEX(B{G0}:B{GE},B5+1)", "0.000"),
          ("Fine bracket index", f"=MIN(MATCH(Inputs!B4,AE{G0}:AE{GE},1),{NG-1})", "0"),
          ("Operating pressure P*, MPa", f"=IF(INDEX(AE{G0}:AE{GE},B8+1)=INDEX(AE{G0}:AE{GE},B8),INDEX(R{G0}:R{GE},B8),"
                                         f"INDEX(R{G0}:R{GE},B8)+(Inputs!B4-INDEX(AE{G0}:AE{GE},B8))*"
                                         f"(INDEX(R{G0}:R{GE},B8+1)-INDEX(R{G0}:R{GE},B8))/"
                                         f"(INDEX(AE{G0}:AE{GE},B8+1)-INDEX(AE{G0}:AE{GE},B8)))", "0.000"),
          ("Σq before normalisation, m³/min", "=SUM(G4:G15)", "0.0000"),
          ("Uniformity index 1 − CoV", "=1-SQRT(SUMPRODUCT((Inputs!D23:D34>0)*(H4:H15-Inputs!B20)^2)/Inputs!B18)/Inputs!B20", "0.000"),
          ("Cluster efficiency (≥50% of equal share)", "=SUMPRODUCT((Inputs!D23:D34>0)*(H4:H15>=0.5*Inputs!B20))/Inputs!B18", "0%"),
          ("Max / min cluster rate", "=MAX(H4:H15)/MIN(IF(SUMPRODUCT((Inputs!D23:D34>0)*1)>0,L4:L15))", "0.00"),
          ("State", "=Inputs!B15", None)]
for i, (lab, f, fmt) in enumerate(labels):
    r = 3 + i
    put(wd, f"A{r}", lab)
    fcell(wd, r, 2, f, fmt, fill_res if r in (9, 11, 12) else None,
          f_bold if r in (9, 11) else None)
# max/min needs active-only min: helper column L
put(wd, "A17", "P* is found on a 201-point pressure grid, refined once between the bracketing points (grids below, row 30+).", f_note)

hdr(wd, 3, 4, ["Cluster", "σ, MPa", "Shots", "q raw", "q, m³/min", "Share", "ΔP_perf, MPa", "ΔP_nwb, MPa",
               "vs equal share", "q (active only)"])
wd.row_dimensions[3].height = 30
for j in range(NC):
    r = 4 + j
    ir = CL0 + j
    fcell(wd, r, 4, f"=Inputs!A{ir}")
    fcell(wd, r, 5, f"=Inputs!C{ir}", "0.00")
    fcell(wd, r, 6, f"=Inputs!D{ir}", "0")
    fcell(wd, r, 7, q_of_p("$B$9", j), "0.0000")
    fcell(wd, r, 8, f"=G{r}*Inputs!$B$4/$B$10", "0.000", fill_res)
    fcell(wd, r, 9, f"=H{r}/Inputs!$B$4", "0.0%", fill_res)
    fcell(wd, r, 10, f"=IF(F{r}>0,Inputs!$B$12*Inputs!$B$5*H{r}^2/(F{r}^2*Inputs!H{ir}^4*Inputs!I{ir}^2),0)", "0.000")
    fcell(wd, r, 11, f"=IF(F{r}>0,Inputs!G{ir}*H{r}^Inputs!$B$11,0)", "0.000")
    fcell(wd, r, 12, f"=IF(F{r}>0,H{r}/Inputs!$B$20-1,0)", "+0.0%;-0.0%;0.0%")
    fcell(wd, r, 13, f"=IF(F{r}>0,H{r},1000000000)", "0.000", fill_help)
wd["B13"] = T("=MAX(H4:H15)/MIN(M4:M15)")
for col, w in zip("DEFGHIJKLM", (12, 10, 7, 10, 11, 9, 12, 12, 13, 11)):
    wd.column_dimensions[col].width = w

hdr(wd, G0 - 1, 1, ["k", "P, MPa"] + [f"=Inputs!A{CL0+j}" for j in range(NC)] + ["Σq"])
hdr(wd, G0 - 1, 17, ["k", "P fine, MPa"] + [f"=Inputs!A{CL0+j}" for j in range(NC)] + ["Σq"])
put(wd, f"A{G0-2}", "Coarse pressure grid", f_bold)
put(wd, f"Q{G0-2}", "Refined grid between bracket points", f_bold)
for k in range(NG):
    r = G0 + k
    fcell(wd, r, 1, k)
    fcell(wd, r, 2, f"=$B$3+($B$4-$B$3)*A{r}/{NG-1}", "0.0000")
    for j in range(NC):
        fcell(wd, r, 3 + j, q_of_p(f"$B{r}", j), "0.000")
    fcell(wd, r, 15, f"=SUM(C{r}:N{r})", "0.000")
    fcell(wd, r, 17, k)
    fcell(wd, r, 18, f"=$B$6+($B$7-$B$6)*Q{r}/{NG-1}", "0.00000")
    for j in range(NC):
        fcell(wd, r, 19 + j, q_of_p(f"$R{r}", j), "0.000")
    fcell(wd, r, 31, f"=SUM(S{r}:AD{r})", "0.0000")

# =====================================================================  ChartData (conceptual LE plot)
wch = wb.create_sheet(sn("ChartData"))
put(wch, "A1", "Chart helpers. #N/A is intentional: Excel skips #N/A points, so switched-off clusters disappear from the chart.", f_note)
put(wch, "A2", "Y = casing pressure minus the lowest active cluster stress (MPa), so the axis autoscale stays readable.", f_note)
hdr(wch, 4, 1, ["q, m³/min"] + [f"=Inputs!A{CL0+j}" for j in range(NC)] + ["P* line", "op q", "op P"])
NCH = 101
for k in range(NCH):
    r = 5 + k
    fcell(wch, r, 1, f"=2.2*Inputs!$B$20*{k}/{NCH-1}", "0.000")
    for j in range(NC):
        ir = CL0 + j
        fcell(wch, r, 2 + j,
              f"=IF(Inputs!$D${ir}=0,NA(),Inputs!$C${ir}+Inputs!$B$12*Inputs!$B$5*$A{r}^2/"
              f"(Inputs!$D${ir}^2*Inputs!$H${ir}^4*Inputs!$I${ir}^2)+Inputs!$G${ir}*$A{r}^Inputs!$B$11-Distribution!$B$3)", "0.000")
    fcell(wch, r, 14, f"=Distribution!$B$9-Distribution!$B$3", "0.000")
for j in range(NC):
    r = 5 + j
    fcell(wch, r, 15, f"=IF(Inputs!D{CL0+j}>0,Distribution!H{4+j},NA())", "0.000")
    fcell(wch, r, 16, f"=IF(Inputs!D{CL0+j}>0,Distribution!$B$9-Distribution!$B$3,NA())", "0.000")

ch = ScatterChart()
ch.title = T("Conceptual LE plot: cluster entry curves vs common casing pressure")
ch.style = 13
ch.x_axis.title = T("cluster rate, m³/min")
ch.y_axis.title = T("pressure above lowest cluster stress, MPa")
ch.height, ch.width = 11, 20
xref = Reference(wch, min_col=1, min_row=5, max_row=4 + NCH)
for j in range(NC):
    s = Series(Reference(wch, min_col=2 + j, min_row=4, max_row=4 + NCH), xref, title_from_data=True)
    s.marker.symbol = "none"
    s.smooth = True
    ch.series.append(s)
s = Series(Reference(wch, min_col=14, min_row=4, max_row=4 + NCH), xref, title_from_data=True)
s.graphicalProperties.line.dashStyle = "dash"
s.graphicalProperties.line.solidFill = "000000"
s.marker.symbol = "none"
ch.series.append(s)
s = Series(Reference(wch, min_col=16, min_row=5, max_row=4 + NC), Reference(wch, min_col=15, min_row=5, max_row=4 + NC),
           title=T("operating points"))
s.marker.symbol = "circle"
s.marker.size = 7
s.graphicalProperties.line.noFill = True
ch.series.append(s)
ch.x_axis.delete = False
ch.y_axis.delete = False
wd.add_chart(ch, "P3")

# =====================================================================  SDT
wsd = wb.create_sheet(sn("SDT"))
put(wsd, "A1", "Step-down / step-rate test analysis:  BHP − ISIP = a·Q² + b·Q^β   (a, b ≥ 0)", f_title)
put(wsd, "A2", "Use pressures at the same time after each rate change. Pre-proppant test fixes open perforations; end-of-stage test measures erosion.", f_note)
sdt = case["sdt"]


def sdt_block(c0, title, steps):
    C = lambda i: L(c0 + i)
    put(wsd, f"{C(0)}3", title, f_bold)
    params = [("ISIP (bottomhole), MPa", sdt["isip_bh_mpa"], "0.00"),
              ("Pressure type (BH/Surface)", "BH", None),
              ("TVD, m (surface only)", 2900, "0"),
              ("Pipe friction k, MPa/(m³/min)^e (surface)", 0.0, "0.0000"),
              ("Pipe friction exponent e", 1.8, "0.00"),
              ("Entry hole d, mm", sdt["d_mm"], "0.00"),
              ("Pressure noise floor, MPa", 0.17, "0.00")]
    for i, (lab, v, fmt) in enumerate(params):
        put(wsd, f"{C(0)}{4+i}", lab)
        inp(wsd, f"{C(1)}{4+i}", v, fmt)
    dv = DataValidation(type="list", formula1=T('"BH,Surface"')); wsd.add_data_validation(dv); dv.add(f"{C(1)}5")
    hdr(wsd, 12, c0, ["Rate, m³/min", "Pressure, MPa", "Use", "BHP, MPa", "y = BHP−ISIP", "x1 = Q²", "x2 = Q^β", "Fit, MPa"])
    for i in range(10):
        r = 13 + i
        st = steps[i] if i < len(steps) else None
        inp(wsd, f"{C(0)}{r}", st[0] if st else None, "0.00")
        inp(wsd, f"{C(1)}{r}", st[1] if st else None, "0.00")
        fcell(wsd, r, c0 + 2, f"=IF(AND(ISNUMBER({C(0)}{r}),ISNUMBER({C(1)}{r})),IF({C(0)}{r}>0,1,0),0)", "0")
        fcell(wsd, r, c0 + 3, f'=IF({C(2)}{r}=1,IF(${C(1)}$5="Surface",{C(1)}{r}+Inputs!$B$5*9.80665*${C(1)}$6/1000000'
                              f'-${C(1)}$7*{C(0)}{r}^${C(1)}$8,{C(1)}{r}),0)', "0.000")
        fcell(wsd, r, c0 + 4, f"=IF({C(2)}{r}=1,{C(3)}{r}-${C(1)}$4,0)", "0.000")
        fcell(wsd, r, c0 + 5, f"=IF({C(2)}{r}=1,{C(0)}{r}^2,0)", "0.000")
        fcell(wsd, r, c0 + 6, f"=IF({C(2)}{r}=1,{C(0)}{r}^Inputs!$B$11,0)", "0.000")
        fcell(wsd, r, c0 + 7, f"=IF({C(2)}{r}=1,${C(1)}$36*{C(5)}{r}+${C(1)}$37*{C(6)}{r},NA())", "0.000")
    rng = lambda i: f"{C(i)}13:{C(i)}22"
    res = [
        (25, "S11 = Σx1²", f"=SUMPRODUCT({rng(5)},{rng(5)})", "0.00E+00"),
        (26, "S12 = Σx1·x2", f"=SUMPRODUCT({rng(5)},{rng(6)})", "0.00E+00"),
        (27, "S22 = Σx2²", f"=SUMPRODUCT({rng(6)},{rng(6)})", "0.00E+00"),
        (28, "T1 = Σx1·y", f"=SUMPRODUCT({rng(5)},{rng(4)})", "0.00E+00"),
        (29, "T2 = Σx2·y", f"=SUMPRODUCT({rng(6)},{rng(4)})", "0.00E+00"),
        (30, "det", f"={C(1)}25*{C(1)}27-{C(1)}26^2", "0.00E+00"),
        (31, "a (two-term)", f"=IF({C(1)}30=0,-1,({C(1)}28*{C(1)}27-{C(1)}29*{C(1)}26)/{C(1)}30)", "0.00000"),
        (32, "b (two-term)", f"=IF({C(1)}30=0,-1,({C(1)}25*{C(1)}29-{C(1)}26*{C(1)}28)/{C(1)}30)", "0.00000"),
        (33, "a (perforations only)", f"=IF({C(1)}25>0,MAX(0,{C(1)}28/{C(1)}25),0)", "0.00000"),
        (34, "b (near-wellbore only)", f"=IF({C(1)}27>0,MAX(0,{C(1)}29/{C(1)}27),0)", "0.00000"),
        (35, "Model used", f'=IF(AND({C(1)}31>=0,{C(1)}32>=0),"two-term",IF({C(1)}33>0,"perforations only","near-wellbore only"))', None),
        (36, "a, MPa/(m³/min)²", f'=IF({C(1)}35="two-term",{C(1)}31,IF({C(1)}35="perforations only",{C(1)}33,0))', "0.00000"),
        (37, "b, MPa/(m³/min)^β", f'=IF({C(1)}35="two-term",{C(1)}32,IF({C(1)}35="near-wellbore only",{C(1)}34,0))', "0.0000"),
        (38, "RMSE, MPa", f"=SQRT(SUMPRODUCT({rng(2)},({rng(4)}-${C(1)}$36*{rng(5)}-${C(1)}$37*{rng(6)})^2)/SUM({rng(2)}))", "0.000"),
        (39, "Max rate, m³/min", f"=MAX({rng(0)})", "0.00"),
        (40, "ΔP_perf at max rate, MPa", f"={C(1)}36*{C(1)}39^2", "0.00"),
        (41, "ΔP_nwb at max rate, MPa", f"={C(1)}37*{C(1)}39^Inputs!$B$11", "0.00"),
        (42, "Resolvable? (ΔP_perf > 4 × noise)", f'=IF({C(1)}40>4*MAX({C(1)}38,{C(1)}10),"YES","NO — perf friction lost in noise")', None),
        (43, "A_eff = n·Cd·d², mm²", f'=IF({C(1)}36>0,SQRT(Inputs!$B$12*Inputs!$B$5/{C(1)}36),"∞")', "0.0"),
    ]
    for r, lab, f, fmt in res:
        put(wsd, f"{C(0)}{r}", lab, f_bold if r in (36, 37, 42, 43) else None)
        fcell(wsd, r, c0 + 1, f, fmt, fill_res if r in (36, 37, 42, 43) else None, f_bold if r in (42, 43) else None)
    put(wsd, f"{C(0)}45", "Open perforations for assumed Cd", f_bold)
    hdr(wsd, 46, c0, ["Cd", "Open perfs n"])
    for i, cd in enumerate((0.56, 0.65, 0.75, 0.85, 0.89)):
        r = 47 + i
        inp(wsd, f"{C(0)}{r}", cd, "0.00")
        fcell(wsd, r, c0 + 1, f'=IF(ISNUMBER({C(1)}43),{C(1)}43/({C(0)}{r}*{C(1)}9^2),"∞")', "0.0", fill_res)
    # chart
    sc = ScatterChart()
    sc.title = T(title)
    sc.style = 13
    sc.x_axis.title = T("rate, m³/min")
    sc.y_axis.title = T("BHP − ISIP, MPa")
    sc.height, sc.width = 7.5, 13
    xr = Reference(wsd, min_col=c0, min_row=13, max_row=22)
    s1 = Series(Reference(wsd, min_col=c0 + 4, min_row=13, max_row=22), xr, title=T("measured"))
    s1.marker.symbol = "circle"; s1.marker.size = 7; s1.graphicalProperties.line.noFill = True
    s2 = Series(Reference(wsd, min_col=c0 + 7, min_row=13, max_row=22), xr, title=T("fit"))
    s2.marker.symbol = "none"; s2.smooth = True
    sc.series += [s1, s2]
    sc.x_axis.delete = False
    sc.y_axis.delete = False
    wsd.add_chart(sc, f"{C(0)}54")
    for i, w in enumerate((36, 13, 6, 10, 12, 10, 10, 10)):
        wsd.column_dimensions[C(i)].width = w


sdt_block(1, "Pre-proppant test (start of stage)", sdt["steps_pre"])
sdt_block(10, "End-of-stage test", sdt["steps_post"])
put(wsd, "A70", "Erosion indicator", f_bold)
put(wsd, "A71", "A_eff end / A_eff start")
fcell(wsd, 71, 2, '=IF(AND(ISNUMBER(B43),ISNUMBER(K43)),K43/B43,"n/a")', "0.00", fill_res)
put(wsd, "A72", "Open perforations from pre-test at Cd0")
fcell(wsd, 72, 2, '=IF(ISNUMBER(B43),B43/(Inputs!B7*B9^2),"n/a")', "0.0", fill_res)
put(wsd, "A73", "Implied end diameter at Cd_max, mm")
fcell(wsd, 73, 2, '=IF(AND(ISNUMBER(B72),ISNUMBER(K43)),SQRT(K43/(B72*Inputs!B8)),"n/a")', "0.00", fill_res)

# =====================================================================  Erosion
we = wb.create_sheet(sn("Erosion"))
put(we, "A1", "Perforation erosion through the pump schedule (lumped, Long et al. 2018)", f_title)
we.column_dimensions["A"].width = 38
pe = [(3, "Open perforations n", '=IF(Inputs!B16="SDT",SDT!B72,Inputs!B19)', "0.0"),
      (4, "Initial diameter d0, mm", "=Inputs!B21", "0.00"),
      (5, "Cd0", "=Inputs!B7", "0.00"),
      (6, "Cd_max", "=Inputs!B8", "0.00"),
      (7, "α used, m²·s/kg", "=Inputs!B14", "0.00E+00"),
      (8, "β, m·s/kg", "=Inputs!B10", "0.00E+00"),
      (9, "Time step, min", "=Inputs!B17", "0.0"),
      (10, "Fluid density, kg/m³", "=Inputs!B5", "0"),
      (11, "Proppant SG", "=Inputs!B6", "0.00"),
      (12, "Total pumping time, min", "=SUM(B16:B35)", "0.0")]
for r, lab, f, fmt in pe:
    put(we, f"A{r}", lab)
    fcell(we, r, 2, f, fmt)
hdr(we, 15, 1, ["Step", "Duration, min", "Rate, m³/min", "Proppant, kg/m³ clean", "Start time, min"])
for i in range(20):
    r = 16 + i
    st = case["schedule"][i] if i < len(case["schedule"]) else None
    fcell(we, r, 1, i + 1)
    inp(we, f"B{r}", st[0] if st else None, "0.0")
    inp(we, f"C{r}", st[1] if st else None, "0.00")
    inp(we, f"D{r}", st[2] if st else None, "0")
    fcell(we, r, 5, "=0" if i == 0 else f"=E{r-1}+N(B{r-1})", "0.0")

T0 = 61
TE = T0 + NT - 1
hdr(we, T0 - 1, 1, ["k", "t end, min", "Active", "Step", "Q, m³/min", "c, kg/m³", "ρ slurry", "C, kg/m³ slurry",
                    "D start, mm", "Cd start", "v, m/s", "ΔD, mm", "D end, mm", "Cd end", "ΔP_perf, MPa",
                    "Proppant per perf, kg", "C·Q²·dt (SI)"])
for k in range(NT):
    r = T0 + k
    fcell(we, r, 1, k + 1)
    fcell(we, r, 2, f"=A{r}*$B$9", "0.0")
    fcell(we, r, 3, f"=IF(B{r}<=$B$12+0.000000001,1,0)", "0")
    fcell(we, r, 4, f"=IF(C{r}=1,MATCH(B{r}-$B$9/2,$E$16:$E$35,1),0)", "0")
    fcell(we, r, 5, f"=IF(C{r}=1,INDEX($C$16:$C$35,D{r}),0)", "0.00")
    fcell(we, r, 6, f"=IF(C{r}=1,N(INDEX($D$16:$D$35,D{r})),0)", "0")
    fcell(we, r, 7, f"=($B$10+F{r})/(1+F{r}/(1000*$B$11))", "0.0")
    fcell(we, r, 8, f"=F{r}/(1+F{r}/(1000*$B$11))", "0.0")
    fcell(we, r, 9, "=$B$4" if k == 0 else f"=M{r-1}", "0.0000")
    fcell(we, r, 10, "=$B$5" if k == 0 else f"=N{r-1}", "0.0000")
    fcell(we, r, 11, f"=(E{r}/60)/($B$3*PI()/4*(I{r}/1000)^2)", "0.00")
    fcell(we, r, 12, f"=C{r}*$B$7*H{r}*K{r}^2*$B$9*60*1000", "0.00000")
    fcell(we, r, 13, f"=I{r}+L{r}", "0.0000")
    fcell(we, r, 14, f"=MIN($B$6,J{r}+C{r}*$B$8*H{r}*K{r}^2*(1-J{r}/$B$6)*$B$9*60)", "0.0000")
    fcell(we, r, 15, f"=IF(C{r}=1,Inputs!$B$12*G{r}*E{r}^2/($B$3^2*M{r}^4*N{r}^2),NA())", "0.000")
    fcell(we, r, 16, ("=0+" if k == 0 else f"=P{r-1}+") + f"C{r}*E{r}/(1+F{r}/(1000*$B$11))*F{r}*$B$9/$B$3", "0.0")
    fcell(we, r, 17, f"=C{r}*H{r}*(E{r}/60)^2*$B$9*60", "0.000E+00")

summ = [(42, "Active time rows", f"=SUM(C{T0}:C{TE})", "0"),
        (43, "ΔP_perf at start, MPa", f"=O{T0}", "0.00"),
        (44, "ΔP_perf at end, MPa", f"=INDEX(O{T0}:O{TE},B42)", "0.00"),
        (45, "D at end, mm", f"=INDEX(M{T0}:M{TE},B42)", "0.00"),
        (46, "Cd at end", f"=INDEX(N{T0}:N{TE},B42)", "0.000"),
        (47, "Friction retained", "=B44/B43", "0.0%"),
        (48, "Proppant per perforation, kg", f"=INDEX(P{T0}:P{TE},B42)", "0"),
        (49, "Effective area n·Cd·d², start → end, mm²", "=B3*B5*B4^2", "0.0"),
        (50, "", "=B3*B46*B45^2", "0.0")]
for r, lab, f, fmt in summ:
    put(we, f"A{r}", lab, f_bold if r in (44, 47) else None)
    fcell(we, r, 2, f, fmt, fill_res, f_bold if r in (44, 47) else None)
put(we, "A41", "Summary", f_bold)
put(we, "A52", "Calibration of α from the two SDTs", f_bold)
cal = [(53, "Open perforations (pre-test, Cd0)", "=SDT!B72", "0.0"),
       (54, "End diameter implied by end test (Cd = Cd_max), mm", "=SDT!B73", "0.00"),
       (55, "∫ C·Q² dt  (SI)", f"=SUM(Q{T0}:Q{TE})", "0.000E+00"),
       (56, "α calibrated, m²·s/kg", '=IF(AND(ISNUMBER(B53),ISNUMBER(B54)),((B54/1000)^5-(SDT!B9/1000)^5)*PI()^2*B53^2/(80*B55),"n/a")',
        "0.00E+00")]
for r, lab, f, fmt in cal:
    put(we, f"A{r}", lab, f_bold if r == 56 else None)
    fcell(we, r, 2, f, fmt, fill_res, f_bold if r == 56 else None)
put(we, "A57", "Set Inputs!B13 = Yes to run the model with this α. β cannot be separated from two SDTs (identifiability) — use the in-stage friction trace (Python).", f_note)
put(we, "A58", "Column O shows #N/A after the schedule ends on purpose (chart gaps).", f_note)

ec = ScatterChart()
ec.title = T("Perforation friction through the stage")
ec.style = 13
ec.x_axis.title = T("time, min")
ec.y_axis.title = T("ΔP_perf, MPa")
ec.height, ec.width = 8, 15
xr = Reference(we, min_col=2, min_row=T0, max_row=TE)
s = Series(Reference(we, min_col=15, min_row=T0 - 1, max_row=TE), xr, title_from_data=True)
s.marker.symbol = "none"
ec.series.append(s)
ec.x_axis.delete = False
ec.y_axis.delete = False
we.add_chart(ec, "G3")
for col, w in zip("BCDEFGHIJKLMNOPQ", [13] * 16):
    we.column_dimensions[col].width = w

# =====================================================================  Design
wg = wb.create_sheet(sn("Design"))
put(wg, "A1", "Design — perforation friction targets", f_title)
wg.column_dimensions["A"].width = 34
hdr(wg, 3, 1, ["Cluster", "Shots", "d, mm", "ΔP_perf start, MPa", "ΔP_perf end (lumped erosion), MPa"])
for j in range(NC):
    r = 4 + j
    ir = CL0 + j
    fcell(wg, r, 1, f"=Inputs!A{ir}")
    fcell(wg, r, 2, f"=Inputs!D{ir}", "0")
    fcell(wg, r, 3, f"=Inputs!E{ir}", "0.00")
    fcell(wg, r, 4, f"=IF(B{r}>0,Inputs!$B$12*Inputs!$B$5*Inputs!$B$20^2/(B{r}^2*C{r}^4*Inputs!F{ir}^2),0)", "0.00")
    fcell(wg, r, 5, f"=IF(B{r}>0,Inputs!$B$12*Inputs!$B$5*Inputs!$B$20^2/(B{r}^2*(C{r}*Erosion!$B$45/Inputs!$B$21)^4*Erosion!$B$46^2),0)", "0.00")
put(wg, "A17", "Stress spread of active clusters, MPa")
fcell(wg, 17, 2, "=MAX(Inputs!L23:L34)-MIN(Inputs!J23:J34)", "0.00")
put(wg, "A18", "Mean ΔP_perf start / stress spread")
fcell(wg, 18, 2, "=SUMIF(B4:B15,\">0\",D4:D15)/Inputs!B18/B17", "0.00")
put(wg, "A19", "Mean ΔP_perf end / stress spread")
fcell(wg, 19, 2, "=SUMIF(B4:B15,\">0\",E4:E15)/Inputs!B18/B17", "0.00")

put(wg, "A22", "Perforation friction matrix at stage rate, MPa (rows: total shots per stage, columns: entry hole, mm)", f_bold)
shots = list(range(12, 73, 6))
dias = [7.6, 8.4, 8.9, 9.5, 10.2, 10.9, 11.4, 12.7]
for block, (r0, cdref, lab) in enumerate(((24, "Inputs!$B$7", "Sharp entry (Cd0)"), (38, "Inputs!$B$8", "Rounded entry (Cd_max, no diameter growth)"))):
    put(wg, f"A{r0-1}", lab, f_bold)
    hdr(wg, r0, 1, ["Total shots \\ d, mm"])
    for i, d in enumerate(dias):
        inp(wg, f"{L(2+i)}{r0}", d, "0.0")
    for k, n in enumerate(shots):
        r = r0 + 1 + k
        inp(wg, f"A{r}", n, "0")
        for i in range(len(dias)):
            col = L(2 + i)
            fcell(wg, r, 2 + i, f"=Inputs!$B$12*Inputs!$B$5*Inputs!$B$4^2/($A{r}^2*{col}${r0}^4*{cdref}^2)", "0.00")
    rng = f"B{r0+1}:{L(1+len(dias))}{r0+len(shots)}"
    wg.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="F8696B", mid_type="percentile",
                                                      mid_value=50, mid_color="FFEB84", end_type="max", end_color="63BE7B"))

put(wg, "A52", "Required total shots for a target friction", f_bold)
put(wg, "A53", "Target ΔP_perf, MPa"); inp(wg, "B53", 10.0, "0.00")
put(wg, "A54", "Entry hole d, mm"); inp(wg, "B54", 10.2, "0.00")
put(wg, "A55", "Cd assumed"); inp(wg, "B55", 0.56, "0.00")
put(wg, "A56", "Required total shots (exact)")
fcell(wg, 56, 2, "=Inputs!B4/(B54^2*B55)*SQRT(Inputs!B12*Inputs!B5/B53)", "0.0", fill_res, f_bold)
put(wg, "A57", "Shots per cluster (exact)")
fcell(wg, 57, 2, "=B56/Inputs!B18", "0.00", fill_res)
put(wg, "A58", "Same target held at END of stage: start target ≈ target / friction retained (Erosion!B47); iterate because retention depends on shots.", f_note)
for col in "BCDEFGHI":
    wg.column_dimensions[col].width = 14

order = ["README", "Inputs", "SDT", "Erosion", "Distribution", "Design", "LE_Curves", "ChartData"]
wb._sheets = [wb[sn(n)] for n in order]
wb.save(OUT)
print("saved", OUT)

"""Plots (log2 axes) and the self-contained HTML report."""
from __future__ import annotations

import base64
import html
import io
import json
import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np


def _plt():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        warnings.warn("matplotlib not installed: report.html will contain tables but no plots")
        return None


def _b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _grid(rows, key):
    isos = sorted({r["iso"] for r in rows})
    exps = sorted({r["exposure_s"] for r in rows})
    Z = np.full((len(isos), len(exps)), np.nan)
    for r in rows:
        Z[isos.index(r["iso"]), exps.index(r["exposure_s"])] = r[key]
    return isos, exps, Z


def _axes_log2(ax, isos, exps):
    lx = np.log2(exps); ly = np.log2(isos)
    ax.set_xlabel("log2 exposure (s)   [ms: " + ", ".join(f"{e * 1e3:.3g}" for e in (exps[0], exps[-1])) + "]")
    ax.set_ylabel("log2 ISO")
    return lx, ly


def _heat(plt, rows, key, title, cmap, center=None, vmin=None, vmax=None):
    isos, exps, Z = _grid(rows, key)
    fig, ax = plt.subplots(figsize=(6, 4.2))
    lx, ly = _axes_log2(ax, isos, exps)
    X, Y = np.meshgrid(lx, ly)
    kw = {}
    if center is not None:
        m = np.nanmax(np.abs(Z - center)) if np.isfinite(Z).any() else 1
        kw = dict(vmin=center - m, vmax=center + m)
    if vmin is not None:
        kw = dict(vmin=vmin, vmax=vmax)
    mesh = ax.pcolormesh(X, Y, Z, shading="nearest", cmap=cmap, **kw)
    fig.colorbar(mesh, ax=ax)
    ax.set_title(title)
    for yi, i in enumerate(isos):
        for xi, e in enumerate(exps):
            if np.isfinite(Z[yi, xi]) and len(isos) * len(exps) <= 100:
                ax.text(lx[xi], ly[yi], f"{Z[yi, xi]:.2f}", ha="center", va="center", fontsize=6)
    return fig


def make_plots(result: dict, raw_result: Optional[dict] = None) -> Dict[str, str]:
    plt = _plt()
    if plt is None:
        return {}
    rows = result["rows"]
    imgs: Dict[str, str] = {}
    # brightness vs log2 exposure per ISO (sim vs real)
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    cmap = plt.get_cmap("viridis")
    isos = sorted({r["iso"] for r in rows})
    for k, iso in enumerate(isos):
        rr = sorted([r for r in rows if r["iso"] == iso], key=lambda r: r["exposure_s"])
        x = [r["log2_exposure"] for r in rr]
        real = [r["reward_midtone_real"] for r in rr]
        sim = [r["reward_midtone_sim"] for r in rr]
        c = cmap(k / max(len(isos) - 1, 1))
        ax.plot(x, real, "-o", color=c, ms=3, label=f"real ISO {iso:g}")
        ax.plot(x, sim, "--x", color=c, ms=3)
    ax.set_xlabel("log2 exposure (s)"); ax.set_ylabel("midtone brightness of centre crop (1 - mean|Y-0.5|)")
    ax.set_title("Brightness vs exposure per ISO (solid real, dashed sim)")
    ax.legend(fontsize=6, ncol=2)
    imgs["brightness"] = _b64(fig)
    imgs["ev_heatmap"] = _b64(_heat(plt, rows, "ev_mean_full", "EV error (log2 sim/real, noise-free render)", "RdBu_r", center=0.0))
    imgs["psnr_heatmap"] = _b64(_heat(plt, rows, "psnr_full", "PSNR (noisy sim vs real) dB", "viridis"))
    if np.isfinite([r["psnr_noise_floor_full"] for r in rows]).any():
        imgs["psnr_floor_heatmap"] = _b64(_heat(plt, rows, "psnr_noise_floor_full", "PSNR real-vs-real (noise floor) dB", "viridis"))
    imgs["nlf_ratio_heatmap"] = _b64(_heat(plt, rows, "nlf_ratio", "Noise-level ratio sim/real (1 = perfect)", "PuOr", center=1.0))
    # NLF overlays
    ex = result.get("extras", {})
    if ex:
        keys = sorted(ex)[:9]
        n = len(keys)
        fig, axs = plt.subplots(3, 3, figsize=(10, 7.5), squeeze=False)
        for ax, k in zip(axs.ravel(), keys):
            e = ex[k]
            ax.loglog(*e["nlf_real"], "o-", ms=3, label="real")
            ax.loglog(*e["nlf_sim"], "x--", ms=3, label="sim")
            ax.set_title(f"ISO {k[0]:g}, {k[1] * 1e3:.3g} ms", fontsize=8)
            ax.set_xlabel("mean (DN)", fontsize=7); ax.set_ylabel("noise var (DN^2)", fontsize=7)
        axs[0, 0].legend(fontsize=7)
        fig.tight_layout()
        imgs["nlf"] = _b64(fig)
    # side-by-side crops
    cr = result.get("crops", {})
    if cr:
        keys = sorted(cr)[:9]
        fig, axs = plt.subplots(len(keys), 3, figsize=(7.5, 2.5 * len(keys)), squeeze=False)
        for r_, k in enumerate(keys):
            real, sim = cr[k]
            diff = ((sim.float() - real.float()).abs() * 4).clamp(0, 255)
            for c_, (im, nm) in enumerate(((real, "real"), (sim, "sim"), (diff, "|diff| x4"))):
                axs[r_, c_].imshow(im.permute(1, 2, 0).to(torch_uint8()).numpy())
                axs[r_, c_].axis("off")
                axs[r_, c_].set_title(f"{nm}  ISO {k[0]:g} {k[1] * 1e3:.3g} ms" if c_ == 0 else nm, fontsize=7)
        fig.tight_layout()
        imgs["crops"] = _b64(fig)
    if raw_result and raw_result.get("rows"):
        rr = raw_result["rows"]
        fig, ax = plt.subplots(figsize=(6, 4))
        for r in rr:
            ax.plot(*r["curve_real"], "o-", ms=3, alpha=.6)
            ax.plot(*r["curve_sim"], "x--", ms=3, alpha=.6)
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel("mean (DN above black)"); ax.set_ylabel("noise variance (DN^2)")
        ax.set_title("Raw-domain variance vs mean (o real, x sim)")
        imgs["raw_ptc"] = _b64(fig)
    return imgs


def torch_uint8():
    import torch
    return torch.uint8


def _fmt(v):
    if isinstance(v, float):
        return "nan" if math.isnan(v) else f"{v:.4g}"
    return html.escape(str(v))


def _table(rows, cols, header=None):
    h = "".join(f"<th>{html.escape(header[i] if header else c)}</th>" for i, c in enumerate(cols))
    body = "".join("<tr>" + "".join(f"<td>{_fmt(r.get(c, ''))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


def write_report(result: dict, out_dir, raw_result: Optional[dict] = None, title: str = "camsim validation") -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    imgs = make_plots(result, raw_result)
    s = result["summary"]
    th = result["thresholds"]
    crit_rows = []
    for name, c in s["criteria"].items():
        status = "n/a" if c.get("pass") is None else ("PASS" if c["pass"] else "FAIL")
        crit_rows.append({"criterion": name, "threshold": c.get("threshold", c.get("threshold_db")),
                          "pass_fraction": c.get("pass_fraction", ""), "status": status, "note": c.get("note", "")})
    css = ("body{font-family:sans-serif;max-width:1100px;margin:2em auto;padding:0 1em}table{border-collapse:collapse;font-size:12px}"
           "td,th{border:1px solid #ccc;padding:2px 6px;text-align:right}th{background:#eee}img{max-width:100%}"
           ".PASS{color:#070}.FAIL{color:#a00}.warn{background:#fff3cd;padding:4px 8px;margin:2px 0}")
    parts = [f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style></head><body>",
             f"<h1>{html.escape(title)}</h1>",
             f"<p>Focus {result['focus_d']:.3g} D, {s['n_points']} grid points ({s['n_evaluated']} evaluated; "
             f"{len(s['excluded'])} excluded for >80 % simulated clipping), K={result['K']} noisy realisations, "
             f"sweep downscale {result['downscale']}. Thresholds are <i>reported, not enforced</i>.</p>",
             "<h2>Pass / fail</h2>", _table(crit_rows, ["criterion", "threshold", "pass_fraction", "status", "note"]),
             f"<p>EV error (noise-free render, full frame): mean|.| {s['ev_error_full']['mean_abs']:.4f}, max|.| "
             f"{s['ev_error_full']['max_abs']:.4f}, bias {s['ev_error_full']['bias']:+.4f} stops. "
             f"Mean PSNR {s['psnr_full_mean']:.2f} dB, SSIM {s['ssim_full_mean']:.4f}.</p>",
             "<h2>Notes and warnings</h2>"]
    for n in result.get("notes", []):
        cls = "warn" if "WARN" in n.upper() or "NOT " in n.upper() else ""
        parts.append(f"<div class='{cls}'>{html.escape(n)}</div>")
    if not imgs:
        parts.append("<div class='warn'>matplotlib missing: plots omitted. See metrics.csv.</div>")
    titles = {"brightness": "Brightness vs log2 exposure", "ev_heatmap": "EV error heatmap (log2 ISO x log2 t)",
              "psnr_heatmap": "PSNR heatmap", "psnr_floor_heatmap": "Noise floor (real vs real) PSNR",
              "nlf_ratio_heatmap": "Noise level ratio", "nlf": "Noise-level functions", "crops": "Side-by-side crops",
              "raw_ptc": "Raw-domain variance vs mean"}
    for k, t in titles.items():
        if k in imgs:
            parts.append(f"<h2>{t}</h2><img src='data:image/png;base64,{imgs[k]}'/>")
    if raw_result and raw_result.get("rows"):
        parts.append("<h2>Raw-domain validation (validation sub-grid)</h2>")
        parts.append(_table(raw_result["rows"], ["file", "iso", "exposure_s", "ev_err_mean", "noise_var_ratio",
                                                 "read_floor_sim", "read_floor_real"]))
    cols = ["iso", "exposure_s", "ev_mean_full", "ev_mean_crop", "psnr_full", "psnr_noise_floor_full", "ssim_full",
            "nlf_ratio", "nps_ratio_mid", "reward_midtone_rel_err", "reward_sobel_rel_err", "reward_tenengrad_rel_err",
            "valid_fraction"]
    parts.append("<h2>All grid points</h2>")
    parts.append(_table(result["rows"], cols))
    parts.append("<h2>Limitations</h2><ul><li>Single-frame source noise is partly baked into the source.</li>"
                 "<li>Source-clipped highlights are masked, not recovered (without a bracket).</li>"
                 "<li>A phone ISP may be scene-adaptive; a global tone curve may not suffice.</li>"
                 "<li>Static scene assumed.</li><li>Raw+YUV sessions may differ from YUV-only sweep sessions.</li></ul>")
    parts.append("</body></html>")
    p = out_dir / "report.html"
    p.write_text("\n".join(parts), encoding="utf-8")
    return p

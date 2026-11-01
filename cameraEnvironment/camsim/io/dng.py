"""RawFrame dataclass and DNG loader.

Pixels come from ``rawpy`` when it is installed (handles every DNG compression), otherwise from
``tifffile`` (uncompressed DNG, which is what Android's DngCreator writes). DNG tags always come
from ``tifffile``. Values in the per-capture JSON sidecar (the full CaptureResult dump written by
the Android capture test) override DNG tags, because they are the HAL's own numbers.
"""
from __future__ import annotations

import struct
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import sidecar as sc

# DNG / TIFF tag codes
T_CFA_DIM, T_CFA_PATTERN = 33421, 33422
T_BLACK_DIM, T_BLACK, T_WHITE = 50713, 50714, 50717
T_AS_SHOT_NEUTRAL = 50728
T_CM1, T_CM2, T_FM1, T_FM2 = 50721, 50722, 50964, 50965
T_ILL1, T_ILL2 = 50778, 50779
T_BASELINE_EXPOSURE = 50730
T_NOISE_PROFILE = 51041
T_OPCODE2, T_OPCODE3 = 51009, 51022
T_ACTIVE_AREA = 50829
T_PHOTOMETRIC = 262
T_EXIF_IFD = 34665
T_EXIF_ISO, T_EXIF_EXPOSURE = 34855, 33434

_CFA_LETTER = {0: "R", 1: "G", 2: "B"}


@dataclass
class RawFrame:
    """A Bayer raw frame with everything the simulator needs.

    ``bayer`` is the raw mosaic in DN *including* the black level (float32, shape HxW).
    ``cfa`` is the 2x2 pattern, row-major, e.g. ``"RGGB"``.
    """
    bayer: np.ndarray
    cfa: str = "RGGB"
    black_2x2: np.ndarray = field(default_factory=lambda: np.zeros((2, 2), np.float32))
    white_level: float = 1023.0
    iso: float = 50.0
    exposure_s: float = 0.1
    focus_d: Optional[float] = None
    as_shot_neutral: Optional[np.ndarray] = None
    color_matrix: Dict[int, np.ndarray] = field(default_factory=dict)      # illuminant code -> 3x3
    forward_matrix: Dict[int, np.ndarray] = field(default_factory=dict)
    noise_profile: Optional[np.ndarray] = None                            # (n_channels, 2) of (S, O)
    baseline_exposure: float = 0.0
    gain_map: Optional[np.ndarray] = None                                 # (gh, gw, 4) per CFA position
    warp_rectilinear: Optional[dict] = None
    wb_gains: Optional[Tuple[float, float, float, float]] = None          # R, Gr(even), Gb(odd), B
    ccm: Optional[np.ndarray] = None                                      # HAL sensor->sRGB 3x3
    post_raw_boost: float = 100.0
    sidecar: Optional[dict] = None
    path: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.bayer = np.asarray(self.bayer, dtype=np.float32)
        self.black_2x2 = np.asarray(self.black_2x2, dtype=np.float32).reshape(2, 2)
        if len(self.cfa) != 4 or any(ch not in "RGB" for ch in self.cfa):
            raise ValueError(f"bad CFA pattern {self.cfa!r}")

    @property
    def shape(self):
        return self.bayer.shape

    @property
    def range_dn(self) -> float:
        return float(self.white_level - float(self.black_2x2.mean()))

    def black_map(self) -> np.ndarray:
        h, w = self.bayer.shape
        return np.tile(self.black_2x2, ((h + 1) // 2, (w + 1) // 2))[:h, :w]

    def channel_positions(self) -> Dict[str, List[Tuple[int, int]]]:
        pos: Dict[str, List[Tuple[int, int]]] = {"R": [], "G": [], "B": []}
        for k, ch in enumerate(self.cfa):
            pos[ch].append((k // 2, k % 2))
        return pos

    def wb_per_position(self, gains: Optional[Sequence[float]] = None) -> np.ndarray:
        """2x2 array of white-balance gains per CFA position (R, Gr=green on the R row, Gb, B)."""
        g = gains if gains is not None else self.wb_gains
        if g is None:
            if self.as_shot_neutral is not None:
                n = np.asarray(self.as_shot_neutral, float)
                n = n / n[1]
                g = (1 / n[0], 1.0, 1.0, 1 / n[2])
            else:
                g = (1.0, 1.0, 1.0, 1.0)
        r, ge, go, b = [float(v) for v in g]
        out = np.ones((2, 2), np.float32)
        r_row = [k // 2 for k, ch in enumerate(self.cfa) if ch == "R"]
        r_row = r_row[0] if r_row else 0
        for k, ch in enumerate(self.cfa):
            i, j = k // 2, k % 2
            out[i, j] = r if ch == "R" else b if ch == "B" else (ge if i == r_row else go)
        return out


# ----------------------------------------------------------------------------------------------
# opcodes
# ----------------------------------------------------------------------------------------------
def parse_opcode_list(blob: bytes) -> List[dict]:
    """Parse a DNG OpcodeList (big-endian). Understands GainMap (9) and WarpRectilinear (1)."""
    ops: List[dict] = []
    if not blob or len(blob) < 4:
        return ops
    (count,) = struct.unpack_from(">I", blob, 0)
    off = 4
    for _ in range(count):
        if off + 16 > len(blob):
            break
        op_id, _ver, flags, nbytes = struct.unpack_from(">IIII", blob, off)
        off += 16
        body = blob[off:off + nbytes]
        off += nbytes
        if op_id == 9 and len(body) >= 76:
            top, left, bottom, right, plane, planes, rpitch, cpitch, nv, nh = struct.unpack_from(">10I", body, 0)
            sv, sh, ov, oh = struct.unpack_from(">4d", body, 40)
            (mp,) = struct.unpack_from(">I", body, 72)
            n = nv * nh * mp
            gains = np.array(struct.unpack_from(f">{n}f", body, 76), np.float32).reshape(nv, nh, mp)
            ops.append(dict(id=9, name="GainMap", top=top, left=left, bottom=bottom, right=right,
                            plane=plane, planes=planes, row_pitch=rpitch, col_pitch=cpitch,
                            spacing=(sv, sh), origin=(ov, oh), gains=gains))
        elif op_id == 1 and len(body) >= 4:
            (np_,) = struct.unpack_from(">I", body, 0)
            coef = np.array(struct.unpack_from(f">{6 * np_}d", body, 4)).reshape(np_, 6)
            cx, cy = struct.unpack_from(">2d", body, 4 + 48 * np_)
            ops.append(dict(id=1, name="WarpRectilinear", coeffs=coef, center=(cx, cy)))
        else:
            ops.append(dict(id=op_id, name=f"op{op_id}", flags=flags, raw=body))
    return ops


def gain_map_to_cfa_planes(op: dict, cfa: str) -> np.ndarray:
    """Convert a GainMap opcode to (gh, gw, 4) gains per 2x2 CFA position.

    A 4-plane map is taken as per-position directly; a 1-plane map applies to all positions;
    Android writes one GainMap per CFA position (row/col pitch 2) which ``parse_opcode_list``
    returns as separate ops - callers combine them via ``combine_gain_maps``.
    """
    g = op["gains"]
    if g.shape[2] >= 4:
        return g[..., :4].astype(np.float32)
    return np.repeat(g[..., :1], 4, axis=2).astype(np.float32)


def combine_gain_maps(ops: List[dict]) -> Optional[np.ndarray]:
    maps = [o for o in ops if o["id"] == 9]
    if not maps:
        return None
    if len(maps) == 1:
        return gain_map_to_cfa_planes(maps[0], "RGGB")
    out = None
    for o in maps:
        pos = (o["top"] % 2) * 2 + (o["left"] % 2)
        g = o["gains"][..., 0]
        if out is None:
            out = np.ones(g.shape + (4,), np.float32)
        if g.shape != out.shape[:2]:
            continue
        out[..., pos] = g
    return out


# ----------------------------------------------------------------------------------------------
# loader
# ----------------------------------------------------------------------------------------------
def _tag(pages, code):
    for p in pages:
        t = p.tags.get(code)
        if t is not None:
            return t
    return None


def _floats(tag, n=None) -> Optional[np.ndarray]:
    if tag is None:
        return None
    v = tag.value
    name = str(getattr(tag, "dtype_name", "")).upper()
    arr = np.asarray(v, dtype=np.float64)
    if "RATIONAL" in name:
        arr = arr.reshape(-1, 2)
        arr = arr[:, 0] / np.where(arr[:, 1] == 0, 1, arr[:, 1])
    elif arr.ndim == 2 and arr.shape[-1] == 2 and n is not None and arr.shape[0] == n:
        arr = arr[:, 0] / np.where(arr[:, 1] == 0, 1, arr[:, 1])
    arr = arr.reshape(-1)
    return arr


def _pick_raw_page(tf):
    cands = []
    for p in tf.pages:
        cands.append(p)
        try:
            for sp in p.pages:  # SubIFDs
                cands.append(sp)
        except Exception:
            pass
    raws = []
    for p in cands:
        try:
            ph = p.tags.get(T_PHOTOMETRIC)
            if ph is not None and int(ph.value) in (32803, 34892) and len(p.shape) == 2:
                raws.append(p)
        except Exception:
            continue
    if not raws:
        raws = [p for p in cands if len(p.shape) == 2 and p.dtype.itemsize >= 2]
    if not raws:
        raise ValueError("no raw (CFA) image found in file")
    return max(raws, key=lambda p: p.shape[0] * p.shape[1]), cands


def load_dng(path, sidecar: Optional[dict] = None, prefer: str = "auto",
             iso: Optional[float] = None, exposure_s: Optional[float] = None,
             focus_d: Optional[float] = None) -> RawFrame:
    """Load a DNG into a :class:`RawFrame`.

    ``prefer``: ``"auto"`` (rawpy if importable, else tifffile), ``"rawpy"`` or ``"tifffile"``.
    ISO / exposure come from (in priority) arguments, sidecar CaptureResult, EXIF.
    """
    import tifffile
    path = Path(path)
    if sidecar is None:
        sidecar = sc.load_sidecar(path)
    notes: List[str] = []

    with tifffile.TiffFile(str(path)) as tf:
        page, cands = _pick_raw_page(tf)
        tag_pages = [tf.pages[0]] + [p for p in cands if p is not tf.pages[0]]
        tf_pixels = None

        cfa_dim = _floats(_tag(tag_pages, T_CFA_DIM))
        cfa_pat = _tag(tag_pages, T_CFA_PATTERN)
        cfa = "RGGB"
        if cfa_pat is not None:
            codes = list(np.asarray(cfa_pat.value).reshape(-1)[:4])
            if len(codes) == 4 and all(int(c) in _CFA_LETTER for c in codes):
                cfa = "".join(_CFA_LETTER[int(c)] for c in codes)
        else:
            notes.append("no CFAPattern tag; assuming RGGB")

        black_tag = _floats(_tag(tag_pages, T_BLACK))
        white_tag = _tag(tag_pages, T_WHITE)
        white = float(np.asarray(white_tag.value).reshape(-1)[0]) if white_tag is not None else None

        asn = _floats(_tag(tag_pages, T_AS_SHOT_NEUTRAL), 3)
        cms, fms = {}, {}
        ill1 = _tag(tag_pages, T_ILL1)
        ill2 = _tag(tag_pages, T_ILL2)
        i1 = int(np.asarray(ill1.value).reshape(-1)[0]) if ill1 is not None else 21
        i2 = int(np.asarray(ill2.value).reshape(-1)[0]) if ill2 is not None else 17
        for code, ill, dst in ((T_CM1, i1, cms), (T_CM2, i2, cms), (T_FM1, i1, fms), (T_FM2, i2, fms)):
            v = _floats(_tag(tag_pages, code), 9)
            if v is not None and v.size == 9:
                dst[ill] = v.reshape(3, 3)
        npf = _floats(_tag(tag_pages, T_NOISE_PROFILE))
        noise_profile = npf.reshape(-1, 2) if npf is not None and npf.size >= 2 else None
        be = _floats(_tag(tag_pages, T_BASELINE_EXPOSURE))

        gain_map, warp = None, None
        for code in (T_OPCODE2, T_OPCODE3):
            t = _tag(tag_pages, code)
            if t is not None:
                blob = bytes(np.asarray(t.value, dtype=np.uint8).tobytes()) if not isinstance(t.value, (bytes, bytearray)) else bytes(t.value)
                ops = parse_opcode_list(blob)
                gm = combine_gain_maps(ops)
                if gm is not None:
                    gain_map = gm
                for o in ops:
                    if o["id"] == 1:
                        warp = o

        exif_iso = exif_exp = None
        try:
            ex = tf.pages[0].tags.get(T_EXIF_IFD)
            if ex is not None and isinstance(ex.value, dict):
                v = ex.value.get("ISOSpeedRatings", ex.value.get(T_EXIF_ISO))
                if v is not None:
                    exif_iso = float(np.asarray(v).reshape(-1)[0])
                e = ex.value.get("ExposureTime", ex.value.get(T_EXIF_EXPOSURE))
                if e is not None:
                    e = np.asarray(e, float).reshape(-1)
                    exif_exp = float(e[0] / e[1]) if e.size == 2 else float(e[0])
        except Exception:
            pass

        want_rawpy = prefer in ("auto", "rawpy")
        pixels, black_rp = None, None
        if want_rawpy:
            try:
                import rawpy
                with rawpy.imread(str(path)) as rp:
                    pixels = np.array(rp.raw_image_visible, dtype=np.float32)
                    desc = rp.color_desc.decode()
                    pat = rp.raw_pattern
                    cfa = "".join(desc[int(pat[i // 2][i % 2])] for i in range(4))
                    cfa = cfa.replace("G", "G")
                    black_rp = np.zeros((2, 2), np.float32)
                    for i in range(2):
                        for j in range(2):
                            black_rp[i, j] = rp.black_level_per_channel[int(pat[i][j])]
                    if white is None:
                        white = float(rp.white_level)
            except ImportError:
                if prefer == "rawpy":
                    raise
            except Exception as e:  # corrupt / unsupported by LibRaw: fall back
                notes.append(f"rawpy failed ({e}); using tifffile pixels")
                pixels = None
        if pixels is None:
            pixels = page.asarray().astype(np.float32)
            aa = _floats(_tag(tag_pages, T_ACTIVE_AREA))
            if aa is not None and aa.size == 4:
                t, l, b, r = [int(x) for x in aa]
                if (b - t, r - l) != pixels.shape and b <= pixels.shape[0] and r <= pixels.shape[1]:
                    pixels = pixels[t:b, l:r]
                    if (t % 2) or (l % 2):
                        notes.append("odd ActiveArea origin: CFA phase may be shifted")

    black = np.zeros((2, 2), np.float32)
    if black_rp is not None:
        black = black_rp
    if black_tag is not None:
        if black_tag.size == 4:
            black = black_tag.reshape(2, 2).astype(np.float32)
        elif black_tag.size == 1:
            black = np.full((2, 2), black_tag[0], np.float32)
    dyn = sc.result_value(sidecar, sc.K_DYN_BLACK)
    if dyn and len(dyn) == 4:
        black = np.asarray(dyn, np.float32).reshape(2, 2)
        notes.append("black level from sidecar dynamicBlackLevel")
    dw = sc.result_value(sidecar, sc.K_DYN_WHITE)
    if white is None:
        white = float(dw) if dw else float(2 ** int(np.ceil(np.log2(max(pixels.max(), 2)))) - 1)
        notes.append(f"no WhiteLevel tag; guessed {white}")

    f_iso = iso or sc.result_value(sidecar, sc.K_ISO) or exif_iso
    exp_ns = sc.result_value(sidecar, sc.K_EXP)
    f_exp = exposure_s or (exp_ns / 1e9 if exp_ns else None) or exif_exp
    f_focus = focus_d if focus_d is not None else sc.result_value(sidecar, sc.K_FOCUS)
    if f_iso is None or f_exp is None:
        raise ValueError(f"{path.name}: cannot determine ISO/exposure (no sidecar, no EXIF); "
                         "pass iso= and exposure_s=")
    ccm = sc.result_value(sidecar, sc.K_CCM)
    ccm = np.asarray(ccm, float).reshape(3, 3) if ccm is not None and len(ccm) == 9 else None
    sc_np = sc.result_value(sidecar, sc.K_NOISE)
    if sc_np:
        noise_profile = np.asarray(sc_np, float).reshape(-1, 2)
    boost = sc.result_value(sidecar, sc.K_BOOST, 100.0)

    return RawFrame(
        bayer=pixels, cfa=cfa, black_2x2=black, white_level=float(white), iso=float(f_iso),
        exposure_s=float(f_exp), focus_d=None if f_focus is None else float(f_focus),
        as_shot_neutral=asn, color_matrix=cms, forward_matrix=fms, noise_profile=noise_profile,
        baseline_exposure=float(be[0]) if be is not None and be.size else 0.0,
        gain_map=gain_map, warp_rectilinear=warp, wb_gains=sc.gains_from_sidecar(sidecar), ccm=ccm,
        post_raw_boost=float(boost) if boost else 100.0, sidecar=sidecar, path=str(path), notes=notes)

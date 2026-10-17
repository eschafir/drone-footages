#!/usr/bin/env python3
"""hdr_lut.py: a 33-point .cube that turns HDR phone footage (BT.2020 HLG or PQ) into Rec.709, for lut3d.
This ffmpeg build has no zscale, so the tone map lives in a LUT like the DJI one:
  signal -> linear light (HLG inverse OETF + its 1000-nit OOTF, or the PQ EOTF), normalised to SDR reference white
  (203 nits) -> a soft knee on the brightest channel from 0.75 (slope 1 at the knee, highlights roll off towards 1, hue kept)
  -> BT.2020 to BT.709 primaries (linear) -> out-of-gamut colours pulled towards their own grey -> BT.1886 (gamma 2.4).
common.grade_vf writes these on first use into assets/luts/generated/ (find_lut never picks that folder).
Usage:
  python3 hdr_lut.py --profile hlg --out hlg-to-rec709.cube
"""
import argparse, os
import numpy as np

N = 33
WHITE = 203.0                                              # SDR reference white in nits (ITU-R BT.2408)
KNEE = 0.75
M2020_709 = np.array([[1.6605, -0.5876, -0.0728], [-0.1246, 1.1329, -0.0083], [-0.0182, -0.1006, 1.1187]])
Y2020 = np.array([0.2627, 0.6780, 0.0593])
Y709 = np.array([0.2126, 0.7152, 0.0722])


def hlg_to_linear(e):
    """HLG signal -> display light relative to SDR white (1000-nit display, system gamma 1.2)."""
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    scene = np.where(e <= 0.5, e * e / 3.0, (np.exp((e - c) / a) + b) / 12.0)
    ys = np.maximum(scene @ Y2020, 1e-6)[..., None]
    return scene * ys ** 0.2 * (1000.0 / WHITE)


def pq_to_linear(e):
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    p = np.power(np.clip(e, 0, 1), 1 / m2)
    return 10000.0 * np.power(np.maximum(p - c1, 0) / (c2 - c3 * p), 1 / m1) / WHITE


def tonemap(rgb):
    """The knee on the brightest channel (maxRGB), the other two scaled with it: a bright saturated colour stays that
    colour instead of washing out to white, which a luminance-based curve does (its channels end up far above 1)."""
    m = np.maximum(rgb.max(axis=-1), 1e-9)
    m2 = np.where(m <= KNEE, m, KNEE + (1 - KNEE) * (1 - np.exp(-(m - KNEE) / (1 - KNEE))))
    return rgb * (m2 / m)[..., None]


def to_709(rgb):
    out = rgb @ M2020_709.T
    y = np.clip(out @ Y709, 0, 1)[..., None]
    # a saturated BT.2020 colour lands outside 709: blend it towards its grey just enough to fit
    lo = np.min(out, axis=-1, keepdims=True)
    hi = np.max(out, axis=-1, keepdims=True)
    k = np.ones_like(y)
    k = np.where(lo < 0, np.minimum(k, y / np.maximum(y - lo, 1e-9)), k)
    k = np.where(hi > 1, np.minimum(k, (1 - y) / np.maximum(hi - y, 1e-9)), k)
    return np.clip(y + (out - y) * np.clip(k, 0, 1), 0, 1)


def build(profile):
    g = np.linspace(0, 1, N)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")                # .cube order: red changes fastest
    sig = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    lin = hlg_to_linear(sig) if profile == "hlg" else pq_to_linear(sig)
    return np.power(to_709(tonemap(lin)), 1 / 2.4)


def write_cube(profile, path):
    if profile not in ("hlg", "pq"):
        raise ValueError("profile must be hlg or pq")
    rgb = build(profile)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        f.write("TITLE \"BT.2020 %s to Rec.709 (drone-edit)\"\nLUT_3D_SIZE %d\n" % (profile.upper(), N))
        f.write("\n".join("%.6f %.6f %.6f" % tuple(v) for v in rgb) + "\n")
    os.replace(tmp, path)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["hlg", "pq"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(write_cube(a.profile, a.out))

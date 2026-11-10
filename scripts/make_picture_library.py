"""Generate the LungDaily picture library: original illustrations (1200x630) for article cards.

Everything is drawn from code (SVG -> rsvg-convert -> JPEG): no photos, no people, no faces,
no third-party artwork, so there are no rights questions. Same seed -> same picture.

  python scripts/make_picture_library.py            # all themes
  python scripts/make_picture_library.py lungs xray # only these themes
  python scripts/make_picture_library.py --sheet    # also write contact sheets to /tmp/library-sheets

Output: content/library/<theme>/<theme>-NN.jpg and content/library/index.json.
Requires rsvg-convert (librsvg). The pipeline only reads the JPEGs, so this runs offline only.
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "content" / "library"
W, H = 1200, 630

PAL = {
    "teal":   dict(bg1="#0E7A80", bg2="#084F53", main="#D6ECED", main2="#7CC3C5", accent="#E4572E", light="#FFFFFF", dark="#053A3D", dk=True),
    "ink":    dict(bg1="#1E3D48", bg2="#0E2027", main="#8AD8D5", main2="#3E9296", accent="#E4572E", light="#E8F6F6", dark="#081419", dk=True),
    "navy":   dict(bg1="#2D4F8C", bg2="#182B52", main="#D4E3F8", main2="#7FA3D6", accent="#E4572E", light="#FFFFFF", dark="#0E1A33", dk=True),
    "dusk":   dict(bg1="#4A2B4E", bg2="#1E1A33", main="#F6C1A8", main2="#B86E80", accent="#E4572E", light="#FDEDE4", dark="#130E21", dk=True),
    "forest": dict(bg1="#3B6A2A", bg2="#1F3B18", main="#E1F0CF", main2="#9CC27F", accent="#E4572E", light="#FFFFFF", dark="#122410", dk=True),
    "plum":   dict(bg1="#74397A", bg2="#4A2150", main="#F3DDF2", main2="#C58AC6", accent="#F07B52", light="#FFFFFF", dark="#2A0F2E", dk=True),
    "sand":   dict(bg1="#F8F1E6", bg2="#EAD9C2", main="#0B6E73", main2="#62AAAB", accent="#E4572E", light="#FFFFFF", dark="#10242B", dk=False),
    "sky":    dict(bg1="#E4F3F3", bg2="#B5DBDC", main="#084F53", main2="#2F8F93", accent="#E4572E", light="#FFFFFF", dark="#10242B", dk=False),
    "mint":   dict(bg1="#EEF7EC", bg2="#CFE8CB", main="#22553A", main2="#5E9E70", accent="#E4572E", light="#FFFFFF", dark="#132A1D", dk=False),
    "coral":  dict(bg1="#F39A6E", bg2="#E4572E", main="#FFF4EE", main2="#FFCDB8", accent="#084F53", light="#FFFFFF", dark="#6E2610", dk=True),
}
DARK = ["teal", "ink", "navy", "dusk", "forest", "plum"]
LIGHT = ["sand", "sky", "mint"]


# ---------------------------------------------------------------- SVG helpers
class Svg:
    def __init__(self, pal: dict, seed: int):
        self.p = pal
        self.r = random.Random(seed)
        self.defs: list[str] = []
        self.body: list[str] = []
        self.n = 0

    def id(self, prefix="g"):
        self.n += 1
        return f"{prefix}{self.n}"

    def add(self, s: str):
        self.body.append(s)

    def lin(self, c1, c2, x1=0, y1=0, x2=0, y2=1, stops=None):
        i = self.id("lg")
        st = stops or [(0, c1, 1), (1, c2, 1)]
        ss = "".join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in st)
        self.defs.append(f'<linearGradient id="{i}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">{ss}</linearGradient>')
        return f"url(#{i})"

    def rad(self, stops, cx=0.5, cy=0.5, r=0.5):
        i = self.id("rg")
        ss = "".join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in stops)
        self.defs.append(f'<radialGradient id="{i}" cx="{cx}" cy="{cy}" r="{r}">{ss}</radialGradient>')
        return f"url(#{i})"

    def blur(self, sd):
        i = self.id("bl")
        self.defs.append(f'<filter id="{i}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="{sd}"/></filter>')
        return f"url(#{i})"

    def glow(self, sd):
        i = self.id("gw")
        self.defs.append(
            f'<filter id="{i}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="{sd}" result="b"/>'
            f'<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>')
        return f"url(#{i})"

    def clip(self, inner: str):
        i = self.id("cp")
        self.defs.append(f'<clipPath id="{i}">{inner}</clipPath>')
        return f"url(#{i})"

    def background(self, angle=None):
        p = self.p
        if angle is None:
            angle = self.r.choice([(0, 0, 0, 1), (0, 0, 1, 1), (1, 0, 0, 1)])
        self.add(f'<rect width="{W}" height="{H}" fill="{self.lin(p["bg1"], p["bg2"], *angle)}"/>')

    def vignette(self, strength=0.35):
        f = self.rad([(0.55, "#000", 0), (1, "#000", strength)], r=0.75)
        self.add(f'<rect width="{W}" height="{H}" fill="{f}"/>')

    def grain(self, amount=0.07):
        i = self.id("gr")
        seed = self.r.randint(1, 999)
        self.defs.append(
            f'<filter id="{i}" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.85" '
            f'numOctaves="2" seed="{seed}"/><feColorMatrix type="saturate" values="0"/>'
            f'<feComponentTransfer><feFuncA type="table" tableValues="0 {amount}"/></feComponentTransfer></filter>')
        self.add(f'<rect width="{W}" height="{H}" filter="url(#{i})"/>')

    def svg(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
                f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>')


def f(x):
    return f"{x:.1f}"


# ---------------------------------------------------------------- shapes
LUNG_L = [  # screen-left lung, medial edge on the right; normalised 0..1
    ("M", 0.70, 0.0),
    ("C", 0.38, 0.03, 0.10, 0.36, 0.03, 0.70),
    ("C", -0.01, 0.90, 0.08, 1.0, 0.28, 0.98),
    ("C", 0.50, 0.96, 0.78, 1.0, 0.95, 0.93),
    ("C", 1.02, 0.70, 1.0, 0.40, 0.91, 0.17),
    ("C", 0.87, 0.06, 0.80, 0.0, 0.70, 0.0),
]
LUNG_R = [  # screen-right lung with cardiac notch, medial edge on the left
    ("M", 0.30, 0.0),
    ("C", 0.20, 0.0, 0.13, 0.06, 0.09, 0.17),
    ("C", 0.03, 0.34, 0.03, 0.48, 0.10, 0.58),
    ("C", 0.20, 0.70, 0.10, 0.80, 0.08, 0.93),
    ("C", 0.26, 1.0, 0.52, 0.96, 0.74, 0.98),
    ("C", 0.93, 1.0, 1.01, 0.90, 0.97, 0.70),
    ("C", 0.90, 0.36, 0.62, 0.03, 0.30, 0.0),
]


def path_d(spec, x0, y0, w, h):
    out = []
    for cmd, *pts in spec:
        xy = [f(x0 + pts[i] * w) + "," + f(y0 + pts[i + 1] * h) for i in range(0, len(pts), 2)]
        out.append(cmd + " " + " ".join(xy))
    return " ".join(out) + " Z"


def path_poly(spec, x0, y0, w, h, steps=16):
    pts, cur = [], None
    for cmd, *c in spec:
        c = [(x0 + c[i] * w, y0 + c[i + 1] * h) for i in range(0, len(c), 2)]
        if cmd == "M":
            cur = c[0]
            pts.append(cur)
        else:
            p0, p1, p2, p3 = cur, c[0], c[1], c[2]
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
                y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
                pts.append((x, y))
            cur = p3
    return pts


def inside(poly, x, y):
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi:
            c = not c
        j = i
    return c


def lung_boxes(cx, cy, size):
    """Two lung boxes for a pair of total height `size`, centred on (cx, cy)."""
    w, h, gap = size * 0.43, size * 0.86, size * 0.07
    top = cy - size / 2 + size * 0.14
    return (cx - gap / 2 - w, top, w, h), (cx + gap / 2, top, w, h)


def bronchial_tree(s: Svg, cx, cy, size, color, width=1.0, depth=6, opacity=0.9, tips=None, spread=1.0):
    """Trachea + recursive bronchi. Returns list of tip points (if tips list given, appended)."""
    r = s.r
    top = cy - size / 2
    carina = cy - size * 0.08
    segs = []
    tw = size * 0.035 * width
    segs.append((cx, top, cx, carina, tw))

    def grow(x, y, ang, ln, wd, d):
        x2 = x + math.cos(math.radians(ang)) * ln
        y2 = y + math.sin(math.radians(ang)) * ln
        segs.append((x, y, x2, y2, wd))
        if d == 0:
            if tips is not None:
                tips.append((x2, y2))
            return
        for sign in (-1, 1):
            a = ang + sign * r.uniform(16, 36) * spread
            # keep growth mostly downward/outward
            a = max(min(a, 175), 5) if ang > 90 or ang < 90 else a
            grow(x2, y2, a, ln * r.uniform(0.66, 0.80), wd * 0.70, d - 1)

    grow(cx, carina, 140 + r.uniform(-6, 6), size * 0.17, tw * 0.75, depth)
    grow(cx, carina, 40 + r.uniform(-6, 6), size * 0.17, tw * 0.75, depth)
    out = [f'<g stroke="{color}" stroke-linecap="round" fill="none" opacity="{opacity}">']
    for x1, y1, x2, y2, wd in segs:
        out.append(f'<line x1="{f(x1)}" y1="{f(y1)}" x2="{f(x2)}" y2="{f(y2)}" stroke-width="{max(wd, 0.8):.2f}"/>')
    out.append("</g>")
    return "".join(out)


def lungs(s: Svg, cx, cy, size, style="filled", fill=None, line=None, tree=True, alveoli=False, opacity=1.0):
    p = s.p
    fill = fill or p["main2"]
    line = line or p["light"]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = lung_boxes(cx, cy, size)
    dl, dr = path_d(LUNG_L, lx, ly, lw, lh), path_d(LUNG_R, rx, ry, rw, rh)
    g = [f'<g opacity="{opacity}">']
    if style == "filled":
        g.append(f'<path d="{dl}" fill="{fill}"/><path d="{dr}" fill="{fill}"/>')
    elif style == "outline":
        sw = max(2, size * 0.008)
        g.append(f'<path d="{dl}" fill="none" stroke="{line}" stroke-width="{sw:.1f}"/>'
                 f'<path d="{dr}" fill="none" stroke="{line}" stroke-width="{sw:.1f}"/>')
    elif style == "soft":
        gr = s.lin(fill, fill, stops=[(0, fill, 0.95), (1, fill, 0.55)])
        g.append(f'<path d="{dl}" fill="{gr}"/><path d="{dr}" fill="{gr}"/>')
    if tree:
        tips: list = []
        cl = s.clip(f'<path d="{dl}"/><path d="{dr}"/><rect x="{f(cx - size*0.03)}" y="{f(cy - size/2)}" width="{f(size*0.06)}" height="{f(size*0.5)}"/>')
        g.append(f'<g clip-path="{cl}">' + bronchial_tree(s, cx, cy, size, line, depth=6, tips=tips, opacity=0.85) + "</g>")
        if alveoli:
            polyL, polyR = path_poly(LUNG_L, lx, ly, lw, lh), path_poly(LUNG_R, rx, ry, rw, rh)
            dots = []
            for x, y in tips:
                if inside(polyL, x, y) or inside(polyR, x, y):
                    rr = size * s.r.uniform(0.006, 0.012)
                    dots.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(rr)}"/>')
            g.append(f'<g fill="{p["accent"]}" opacity="0.9">{"".join(dots)}</g>')
    g.append("</g>")
    s.add("".join(g))
    return (lx, ly, lw, lh), (rx, ry, rw, rh)


def stipple_lungs(s: Svg, cx, cy, size, n=1600, color=None, rmax=3.2):
    color = color or s.p["main"]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = lung_boxes(cx, cy, size)
    polys = [path_poly(LUNG_L, lx, ly, lw, lh), path_poly(LUNG_R, rx, ry, rw, rh)]
    dots = []
    tries = 0
    while len(dots) < n and tries < n * 6:
        tries += 1
        x = s.r.uniform(lx, rx + rw)
        y = s.r.uniform(ly, ly + lh)
        if any(inside(pg, x, y) for pg in polys):
            # denser/larger toward the bottom (gravity-dependent perfusion — and it looks nicer)
            k = (y - ly) / lh
            rr = s.r.uniform(0.6, rmax) * (0.6 + 0.6 * k)
            dots.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(rr)}" opacity="{s.r.uniform(0.35, 1):.2f}"/>')
    s.add(f'<g fill="{color}">{"".join(dots)}</g>')


def sun(s: Svg, cx, cy, r, color=None, glow=True):
    color = color or s.p["accent"]
    if glow:
        g = s.rad([(0, color, 0.55), (1, color, 0)])
        s.add(f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r * 3)}" fill="{g}"/>')
    s.add(f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="{color}"/>')


def particles(s: Svg, n, color, x0=0, y0=0, x1=W, y1=H, rmin=1, rmax=5, blur=None, opmin=0.2, opmax=0.8):
    fl = f' filter="{s.blur(blur)}"' if blur else ""
    dots = "".join(
        f'<circle cx="{f(s.r.uniform(x0, x1))}" cy="{f(s.r.uniform(y0, y1))}" r="{f(s.r.uniform(rmin, rmax))}" '
        f'opacity="{s.r.uniform(opmin, opmax):.2f}"/>' for _ in range(n))
    s.add(f'<g fill="{color}"{fl}>{dots}</g>')


def wave_lines(s: Svg, n, color, y0, y1, amp=30, op=0.35, sw=2, freq=None):
    out = []
    for i in range(n):
        y = y0 + (y1 - y0) * i / max(n - 1, 1)
        fr = freq or s.r.uniform(1.5, 3)
        ph = s.r.uniform(0, 6.28)
        pts = []
        for k in range(0, 61):
            x = -20 + (W + 40) * k / 60
            pts.append(f"{f(x)},{f(y + amp * math.sin(ph + fr * 6.28 * k / 60))}")
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{sw}" opacity="{op}"/>')
    s.add("".join(out))


def pulse_line(s: Svg, y, color, x0=0, x1=W, beats=3, amp=110, sw=5, op=1.0):
    """ECG-like trace (the 'Pulse' in the tagline)."""
    pts = [(x0, y)]
    span = (x1 - x0) / beats
    for b in range(beats):
        bx = x0 + b * span + span * s.r.uniform(0.35, 0.55)
        pts += [(bx - 60, y), (bx - 45, y - amp * 0.18), (bx - 32, y), (bx - 14, y), (bx - 6, y + amp * 0.25),
                (bx + 6, y - amp), (bx + 18, y + amp * 0.45), (bx + 28, y), (bx + 60, y), (bx + 80, y - amp * 0.22), (bx + 104, y)]
    pts.append((x1, y))
    d = " ".join(f"{f(x)},{f(yy)}" for x, yy in pts)
    s.add(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round" '
          f'stroke-linecap="round" opacity="{op}" filter="{s.glow(4)}"/>')


def skyline(s: Svg, base, hmin, hmax, color, windows=None, x0=-20, x1=W + 20, chimney=0, wmin=40, wmax=110, win_op=0.5):
    x = x0
    out, chims = [], []
    while x < x1:
        w = s.r.uniform(wmin, wmax)
        h = s.r.uniform(hmin, hmax)
        out.append(f'<rect x="{f(x)}" y="{f(base - h)}" width="{f(w + 1)}" height="{f(h + 400)}" fill="{color}"/>')
        roof = s.r.random()
        if roof < 0.15:
            out.append(f'<polygon points="{f(x)},{f(base-h)} {f(x+w/2)},{f(base-h-w*0.35)} {f(x+w)},{f(base-h)}" fill="{color}"/>')
        elif roof < 0.3:
            out.append(f'<rect x="{f(x+w*0.45)}" y="{f(base-h-40)}" width="3" height="40" fill="{color}"/>')
        if windows:
            cols = max(2, int(w // 16))
            rows = int(h // 22)
            for cc in range(cols):
                for rr in range(rows):
                    if s.r.random() < 0.45:
                        out.append(f'<rect x="{f(x + 7 + cc * (w - 10) / cols)}" y="{f(base - h + 10 + rr * 22)}" width="5" height="9" '
                                   f'fill="{windows}" opacity="{s.r.uniform(0.2, win_op):.2f}"/>')
        if chimney and s.r.random() < chimney:
            cw = s.r.uniform(14, 24)
            chh = s.r.uniform(60, 150)
            cxx = x + s.r.uniform(5, max(6, w - cw))
            out.append(f'<rect x="{f(cxx)}" y="{f(base - h - chh)}" width="{f(cw)}" height="{f(chh + 2)}" fill="{color}"/>')
            chims.append((cxx + cw / 2, base - h - chh, cw))
        x += w + s.r.uniform(-4, 10)
    s.add("".join(out))
    return chims


def plume(s: Svg, x, y, color, length=500, width=30, drift=1, op=0.5):
    blobs = []
    for k in range(26):
        t = k / 25
        px = x + drift * length * t + s.r.uniform(-10, 10)
        py = y - 220 * (t ** 0.6) + s.r.uniform(-8, 8)
        rr = width * (0.6 + 3.2 * t)
        blobs.append(f'<circle cx="{f(px)}" cy="{f(py)}" r="{f(rr)}" opacity="{op * (1 - t * 0.85):.2f}"/>')
    s.add(f'<g fill="{color}" filter="{s.blur(14)}">{"".join(blobs)}</g>')


def tree_round(s: Svg, x, base, h, trunk, leaf, leaf2=None):
    rr = h * 0.33
    s.add(f'<rect x="{f(x - h*0.03)}" y="{f(base - h*0.55)}" width="{f(h*0.06)}" height="{f(h*0.55)}" fill="{trunk}"/>'
          f'<circle cx="{f(x)}" cy="{f(base - h*0.68)}" r="{f(rr)}" fill="{leaf}"/>')
    if leaf2:
        s.add(f'<circle cx="{f(x - rr*0.35)}" cy="{f(base - h*0.75)}" r="{f(rr*0.45)}" fill="{leaf2}" opacity="0.6"/>')


def tree_pine(s: Svg, x, base, h, color):
    w = h * 0.42
    s.add(f'<polygon points="{f(x)},{f(base-h)} {f(x-w/2)},{f(base-h*0.12)} {f(x+w/2)},{f(base-h*0.12)}" fill="{color}"/>'
          f'<rect x="{f(x-h*0.025)}" y="{f(base-h*0.14)}" width="{f(h*0.05)}" height="{f(h*0.14)}" fill="{color}"/>')


def hills(s: Svg, base, amp, color, n=5, op=1.0):
    pts = [(-10, H + 10), (-10, base)]
    k = s.r.uniform(0, 6)
    for i in range(0, 41):
        x = -10 + (W + 20) * i / 40
        y = base - amp * (0.5 + 0.5 * math.sin(k + i * n * 6.28 / 40 / 3)) * (0.6 + 0.4 * math.sin(k * 2 + i / 7))
        pts.append((x, y))
    pts.append((W + 10, H + 10))
    s.add(f'<polygon points="{" ".join(f"{f(x)},{f(y)}" for x, y in pts)}" fill="{color}" opacity="{op}"/>')


def mountains(s: Svg, base, hmax, color, peaks=6, op=1.0):
    pts = [(-10, H + 10), (-10, base - s.r.uniform(0, hmax * 0.4))]
    x = -10
    while x < W + 10:
        x += s.r.uniform(60, 220)
        pts.append((x, base - s.r.uniform(hmax * 0.3, hmax)))
        x += s.r.uniform(60, 200)
        pts.append((x, base - s.r.uniform(0, hmax * 0.35)))
    pts.append((W + 10, H + 10))
    s.add(f'<polygon points="{" ".join(f"{f(x)},{f(y)}" for x, y in pts)}" fill="{color}" opacity="{op}"/>')


def cloud(s: Svg, x, y, sc, color, op=0.9):
    s.add(f'<g fill="{color}" opacity="{op}"><ellipse cx="{f(x)}" cy="{f(y)}" rx="{f(60*sc)}" ry="{f(22*sc)}"/>'
          f'<circle cx="{f(x-20*sc)}" cy="{f(y-14*sc)}" r="{f(24*sc)}"/><circle cx="{f(x+18*sc)}" cy="{f(y-20*sc)}" r="{f(30*sc)}"/></g>')


def capsule(s: Svg, x, y, ln, wd, ang, c1, c2, shadow=True):
    r = wd / 2
    g = (f'<g transform="translate({f(x)},{f(y)}) rotate({f(ang)})">')
    if shadow:
        g += f'<rect x="{f(-ln/2+6)}" y="{f(-r+10)}" width="{f(ln)}" height="{f(wd)}" rx="{f(r)}" fill="#000" opacity="0.18"/>'
    g += (f'<rect x="{f(-ln/2)}" y="{f(-r)}" width="{f(ln)}" height="{f(wd)}" rx="{f(r)}" fill="{c1}"/>'
          f'<path d="M 0 {f(-r)} H {f(ln/2 - r)} A {f(r)} {f(r)} 0 0 1 {f(ln/2 - r)} {f(r)} H 0 Z" fill="{c2}"/>'
          f'<rect x="{f(-ln/2 + r*0.6)}" y="{f(-r*0.62)}" width="{f(ln - r*1.2)}" height="{f(r*0.28)}" rx="{f(r*0.14)}" fill="#fff" opacity="0.35"/></g>')
    s.add(g)


def tablet(s: Svg, x, y, r, color, ang=0, shadow=True):
    g = f'<g transform="translate({f(x)},{f(y)}) rotate({f(ang)})">'
    if shadow:
        g += f'<circle cx="5" cy="9" r="{f(r)}" fill="#000" opacity="0.18"/>'
    g += (f'<circle r="{f(r)}" fill="{color}"/><circle r="{f(r*0.82)}" fill="#fff" opacity="0.12"/>'
          f'<line x1="{f(-r*0.7)}" y1="0" x2="{f(r*0.7)}" y2="0" stroke="#000" stroke-opacity="0.15" stroke-width="{f(r*0.08)}"/></g>')
    s.add(g)


def molecule(s: Svg, cx, cy, size, color, atom=None, rings=3, sw=4, op=1.0):
    """Fused hexagonal rings with a few substituents."""
    atom = atom or color
    rr = size
    centers = [(cx, cy)]
    dirs = [(math.sqrt(3) * rr, 0), (math.sqrt(3) * rr / 2, 1.5 * rr), (-math.sqrt(3) * rr / 2, 1.5 * rr),
            (math.sqrt(3) * rr / 2, -1.5 * rr)]
    while len(centers) < rings:
        bx, by = s.r.choice(centers)
        dx, dy = s.r.choice(dirs)
        c = (bx + dx, by + dy)
        if all(abs(c[0] - a) + abs(c[1] - b) > 1 for a, b in centers):
            centers.append(c)
    verts = set()
    out = [f'<g opacity="{op}">']
    for x, y in centers:
        pts = [(x + rr * math.cos(math.radians(90 + 60 * k)), y + rr * math.sin(math.radians(90 + 60 * k))) for k in range(6)]
        out.append(f'<polygon points="{" ".join(f"{f(a)},{f(b)}" for a, b in pts)}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round"/>')
        for a, b in pts:
            verts.add((round(a), round(b)))
    verts = sorted(verts)
    for a, b in s.r.sample(verts, min(4, len(verts))):
        ang = math.atan2(b - cy, a - cx)
        a2, b2 = a + math.cos(ang) * rr * 0.9, b + math.sin(ang) * rr * 0.9
        out.append(f'<line x1="{a}" y1="{b}" x2="{f(a2)}" y2="{f(b2)}" stroke="{color}" stroke-width="{sw}"/>'
                   f'<circle cx="{f(a2)}" cy="{f(b2)}" r="{f(sw*2.6)}" fill="{atom}"/>')
    for a, b in verts:
        out.append(f'<circle cx="{a}" cy="{b}" r="{f(sw*1.3)}" fill="{color}"/>')
    out.append("</g>")
    s.add("".join(out))


def hex_pattern(s: Svg, color, size=40, op=0.08, sw=1.5):
    out = []
    dx, dy = math.sqrt(3) * size, 1.5 * size
    for j in range(-1, int(H / dy) + 2):
        for i in range(-1, int(W / dx) + 2):
            x = i * dx + (dx / 2 if j % 2 else 0)
            y = j * dy
            pts = [(x + size * math.cos(math.radians(90 + 60 * k)), y + size * math.sin(math.radians(90 + 60 * k))) for k in range(6)]
            out.append(f'<polygon points="{" ".join(f"{f(a)},{f(b)}" for a, b in pts)}"/>')
    s.add(f'<g fill="none" stroke="{color}" stroke-width="{sw}" opacity="{op}">{"".join(out)}</g>')


def grid(s: Svg, color, step=40, op=0.08, sw=1):
    out = [f'<line x1="{x}" y1="0" x2="{x}" y2="{H}"/>' for x in range(0, W + 1, step)]
    out += [f'<line x1="0" y1="{y}" x2="{W}" y2="{y}"/>' for y in range(0, H + 1, step)]
    s.add(f'<g stroke="{color}" stroke-width="{sw}" opacity="{op}">{"".join(out)}</g>')


def dot_grid(s: Svg, color, step=28, r=1.6, op=0.18):
    out = [f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x in range(step // 2, W, step) for y in range(step // 2, H, step)]
    s.add(f'<g fill="{color}" opacity="{op}">{"".join(out)}</g>')


def side(s: Svg):
    """Horizontal position for the hero element: left, centre or right third (cards put text bottom-left)."""
    return s.r.choice([W * 0.30, W * 0.5, W * 0.70, W * 0.72])


# ---------------------------------------------------------------- themes
def t_lungs(s: Svg, v: int):
    p = s.p
    s.background()
    kind = v % 6
    cx = side(s)
    if kind == 0:  # filled lungs + rising sun between
        dot_grid(s, p["main"], op=0.12)
        sun(s, cx, 250, 46)
        lungs(s, cx, 300, 470, "filled", fill=p["main2"], line=p["light"], alveoli=True)
    elif kind == 1:  # outline lungs, glowing tree
        grid(s, p["main"], op=0.06)
        lungs(s, cx, 315, 500, "outline", line=p["main"], tree=True, alveoli=True)
    elif kind == 2:  # stipple
        stipple_lungs(s, cx, 315, 520, n=1900, color=p["main"])
        s.add(bronchial_tree(s, cx, 315, 520, p["accent"], width=0.8, depth=4, opacity=0.9))
    elif kind == 3:  # big soft lungs cropped, close up
        lungs(s, cx, 360, 900, "soft", fill=p["main2"], line=p["light"], alveoli=True)
    elif kind == 4:  # pattern of small lungs, one highlighted
        hi = (s.r.randint(1, 7), s.r.randint(0, 2))
        for i in range(-1, 10):
            for j in range(0, 4):
                x = 60 + i * 135 + (60 if j % 2 else 0)
                y = 60 + j * 170
                colr = p["accent"] if (i, j) == hi else p["main2"]
                lungs(s, x, y, 120, "filled", fill=colr, tree=False, opacity=1 if (i, j) == hi else 0.45)
    else:  # lungs with pulse line
        lungs(s, cx, 300, 480, "soft", fill=p["main2"], line=p["light"], opacity=0.85)
        pulse_line(s, 330, p["accent"], beats=2, amp=130, sw=6)
    s.grain(0.06)


def chest_film(s: Svg, cx, top, sc, tint, bone, nodule=False):
    """Stylised chest radiograph (bones light, lung fields dark). Returns svg string."""
    g = []
    bw = 520 * sc
    g.append(f'<path d="M {f(cx-bw*0.42)} {f(top)} C {f(cx-bw*0.62)} {f(top+80*sc)}, {f(cx-bw*0.62)} {f(top+400*sc)}, {f(cx-bw*0.55)} {f(top+700*sc)} '
             f'L {f(cx+bw*0.55)} {f(top+700*sc)} C {f(cx+bw*0.62)} {f(top+400*sc)}, {f(cx+bw*0.62)} {f(top+80*sc)}, {f(cx+bw*0.42)} {f(top)} Z" '
             f'fill="{bone}" opacity="0.16" filter="{s.blur(18*sc)}"/>')
    size = 560 * sc
    (lx, ly, lw, lh), (rx, ry, rw, rh) = lung_boxes(cx, top + size * 0.5 + 10 * sc, size)
    g.append(f'<g fill="{tint}" opacity="0.85" filter="{s.blur(10*sc)}"><path d="{path_d(LUNG_L, lx, ly, lw, lh)}"/><path d="{path_d(LUNG_R, rx, ry, rw, rh)}"/></g>')
    g.append(f'<g filter="{s.blur(1.2*sc)}">' + bronchial_tree(s, cx, top + size * 0.5 + 10 * sc, size, bone, width=0.5, depth=6, opacity=0.22) + "</g>")
    g.append(f'<ellipse cx="{f(cx + 40*sc)}" cy="{f(top + 400*sc)}" rx="{f(120*sc)}" ry="{f(110*sc)}" fill="{bone}" opacity="0.25" filter="{s.blur(16*sc)}"/>')
    for k in range(14):
        yy = top + k * 42 * sc
        g.append(f'<rect x="{f(cx - 18*sc)}" y="{f(yy)}" width="{f(36*sc)}" height="{f(34*sc)}" rx="{f(8*sc)}" fill="{bone}" opacity="0.32" filter="{s.blur(2.5*sc)}"/>')
    rib = []
    wob = s.r.uniform(0.9, 1.1)
    for k in range(10):
        yy = top + 60 * sc + k * 44 * sc
        spread = (0.30 + 0.07 * min(k, 5)) * bw * wob
        for sg in (-1, 1):
            x0 = cx + sg * 22 * sc
            rib.append(f'<path d="M {f(x0)} {f(yy)} C {f(x0 + sg*spread*0.7)} {f(yy - 40*sc)}, {f(x0 + sg*spread*1.15)} {f(yy + 10*sc)}, '
                       f'{f(x0 + sg*spread*0.95)} {f(yy + 95*sc)}" fill="none" stroke="{bone}" stroke-width="{f(15*sc)}" stroke-linecap="round" opacity="0.30"/>')
    g.append(f'<g filter="{s.blur(2*sc)}">{"".join(rib)}</g>')
    for sg in (-1, 1):
        g.append(f'<path d="M {f(cx + sg*30*sc)} {f(top + 45*sc)} Q {f(cx + sg*150*sc)} {f(top + 15*sc)} {f(cx + sg*250*sc)} {f(top + 35*sc)}" '
                 f'fill="none" stroke="{bone}" stroke-width="{f(16*sc)}" stroke-linecap="round" opacity="0.4" filter="{s.blur(2*sc)}"/>')
    if nodule:
        nx = cx + s.r.choice([-1, 1]) * s.r.uniform(90, 170) * sc
        ny = top + s.r.uniform(170, 330) * sc
        g.append(f'<circle cx="{f(nx)}" cy="{f(ny)}" r="{f(9*sc)}" fill="{bone}" opacity="0.6" filter="{s.blur(2*sc)}"/>'
                 f'<circle cx="{f(nx)}" cy="{f(ny)}" r="{f(max(26, 38*sc))}" fill="none" stroke="{s.p["accent"]}" stroke-width="4"/>')
    return "".join(g)


XRAY_TINTS = [("#0b1418", "#e9f1f2"), ("#06191d", "#c9ecea"), ("#0b1220", "#d4e2f6"), ("#17120c", "#f3e6d2"),
              ("#101318", "#f2efe9"), ("#041a1c", "#a9e3df")]


def t_xray(s: Svg, v: int):
    tint, bone = XRAY_TINTS[v % len(XRAY_TINTS)]
    kind = v % 4
    negative = False  # inverted films looked washed out
    s.add(f'<rect width="{W}" height="{H}" fill="{tint}"/>')
    body = []
    if kind == 0:  # whole chest, left/centre/right
        cx = s.r.choice([W * 0.5, W * 0.64, W * 0.36])
        body.append(chest_film(s, cx, 30, 1.0, tint, bone, nodule=v % 3 == 0))
    elif kind == 1:  # close-up
        cx = s.r.choice([W * 0.45, W * 0.55])
        body.append(chest_film(s, cx, -s.r.uniform(120, 260), 1.75, tint, bone, nodule=True))
    elif kind == 2:  # one film on a lightbox
        s.add(f'<rect width="{W}" height="{H}" fill="{s.lin(s.p["bg1"], s.p["bg2"])}"/>')
        fx = s.r.choice([600, 720, 480])
        cl = s.clip(f'<rect x="{fx-300}" y="20" width="600" height="600" rx="8"/>')
        body.append(f'<g transform="rotate({f(s.r.uniform(-4, 4))} {fx} 315)"><rect x="{fx-300}" y="20" width="600" height="600" rx="8" fill="{tint}"/>'
                    f'<g clip-path="{cl}">{chest_film(s, fx, 60, 0.78, tint, bone, nodule=v % 2 == 0)}</g></g>')
    else:  # two films side by side on a lit panel
        s.add(f'<rect width="{W}" height="{H}" fill="{s.lin("#e9f3f3", "#bcdcdc") if not negative else s.lin(s.p["bg1"], s.p["bg2"])}"/>')
        for k, fx in enumerate((330, 870)):
            cl = s.clip(f'<rect x="{fx-250}" y="35" width="500" height="560" rx="6"/>')
            body.append(f'<g transform="rotate({f(s.r.uniform(-2.5, 2.5))} {fx} 315)"><rect x="{fx-250}" y="35" width="500" height="560" rx="6" fill="{tint}"/>'
                        f'<g clip-path="{cl}">{chest_film(s, fx, 70, 0.66, tint, bone, nodule=k == 1)}</g></g>')
    if negative:
        i = s.id("neg")
        s.defs.append(f'<filter id="{i}"><feColorMatrix type="matrix" values="-1 0 0 0 1  0 -1 0 0 1  0 0 -1 0 1  0 0 0 1 0"/></filter>')
        s.add(f'<g filter="url(#{i})"><rect width="{W}" height="{H}" fill="{tint}"/>{"".join(body)}</g>')
    else:
        s.add("".join(body))
    if kind in (0, 1) and not negative:
        ticks = "".join(f'<line x1="{W-40}" y1="{y}" x2="{W-(28 if y % 100 else 18)}" y2="{y}"/>' for y in range(40, H - 20, 20))
        s.add(f'<g stroke="{bone}" stroke-width="1.5" opacity="0.35">{ticks}</g>')
    s.grain(0.12)
    s.vignette(0.45 if not negative else 0.15)


def ct_slice(s: Svg, cx, cy, sc, tone, accent, nodule=True):
    g = []
    bw, bh = 300 * sc, 230 * sc
    g.append(f'<ellipse cx="{f(cx)}" cy="{f(cy)}" rx="{f(bw)}" ry="{f(bh)}" fill="{tone}" opacity="0.55" filter="{s.blur(3*sc)}"/>')
    g.append(f'<ellipse cx="{f(cx)}" cy="{f(cy)}" rx="{f(bw*0.9)}" ry="{f(bh*0.86)}" fill="{tone}" opacity="0.25"/>')
    for sg in (-1, 1):
        lx = cx + sg * bw * 0.42
        g.append(f'<path d="M {f(lx - sg*bw*0.05)} {f(cy - bh*0.68)} C {f(lx + sg*bw*0.38)} {f(cy - bh*0.75)}, {f(lx + sg*bw*0.45)} {f(cy + bh*0.55)}, '
                 f'{f(lx + sg*bw*0.12)} {f(cy + bh*0.66)} C {f(lx - sg*bw*0.15)} {f(cy + bh*0.75)}, {f(lx - sg*bw*0.22)} {f(cy - bh*0.1)}, '
                 f'{f(lx - sg*bw*0.05)} {f(cy - bh*0.68)} Z" fill="#0b1316" opacity="0.95" filter="{s.blur(2.5*sc)}"/>')
        ves = []
        for _ in range(int(45 * min(sc, 1.5)) + 6):
            vx = lx + s.r.uniform(-bw * 0.18, bw * 0.3) * sg
            vy = cy + s.r.uniform(-bh * 0.5, bh * 0.55)
            ves.append(f'<circle cx="{f(vx)}" cy="{f(vy)}" r="{f(s.r.uniform(1, 4) * max(sc, .6))}" opacity="{s.r.uniform(.25,.6):.2f}"/>')
        # a few branching vessels
        for _ in range(int(5 * sc) + 2):
            vx, vy = lx + s.r.uniform(-bw * 0.1, bw * 0.2) * sg, cy + s.r.uniform(-bh * 0.4, bh * 0.4)
            ves.append(f'<path d="M {f(vx)} {f(vy)} q {f(sg*20*sc)} {f(-10*sc)} {f(sg*40*sc)} {f(s.r.uniform(-30,30)*sc)}" fill="none" stroke="{tone}" stroke-width="{f(2*sc)}" opacity="0.4"/>')
        g.append(f'<g fill="{tone}">{"".join(ves)}</g>')
    g.append(f'<ellipse cx="{f(cx + bw*0.08)}" cy="{f(cy + bh*0.05)}" rx="{f(bw*0.2)}" ry="{f(bh*0.28)}" fill="{tone}" opacity="0.45" filter="{s.blur(3*sc)}"/>')
    g.append(f'<circle cx="{f(cx)}" cy="{f(cy + bh*0.7)}" r="{f(bh*0.16)}" fill="#f4f6f6" opacity="0.75" filter="{s.blur(1.5*sc)}"/>')
    if nodule:
        nx, ny = cx - bw * 0.45 * s.r.choice([1, -0.9]), cy - bh * s.r.uniform(0.0, 0.35)
        g.append(f'<circle cx="{f(nx)}" cy="{f(ny)}" r="{f(7*max(sc,.6))}" fill="#fff" opacity="0.8"/>'
                 f'<circle cx="{f(nx)}" cy="{f(ny)}" r="{f(30*max(sc,.6))}" fill="none" stroke="{accent}" stroke-width="3"/>')
    s.add("".join(g))


def t_ctscan(s: Svg, v: int):
    p = s.p
    bgs = ["#0a1216", "#06181b", "#0b1020", "#101010"]
    tones = ["#cfd8da", "#cde9e4", "#d3dcef", "#e6e0d6"]
    bg, tone = bgs[v % 4], tones[v % 4]
    kind = v % 4
    s.add(f'<rect width="{W}" height="{H}" fill="{bg}"/>')
    if kind == 0:  # single slice with viewer marks
        cx = W * s.r.choice([0.5, 0.64, 0.36])
        ct_slice(s, cx, 300, 1.0, tone, p["accent"], nodule=v % 3 != 1)
        s.add(f'<g fill="none" stroke="{p["main2"]}" stroke-width="2" stroke-dasharray="4 10" opacity="0.35">'
              f'<circle cx="{f(cx)}" cy="300" r="380"/><circle cx="{f(cx)}" cy="300" r="420"/></g>')
    elif kind == 1:  # row of slices
        for i, cx in enumerate([W * 0.2, W * 0.5, W * 0.8]):
            ct_slice(s, cx, 300, 0.48, tone, p["accent"], nodule=(v + i) % 2 == 0)
            s.add(f'<rect x="{f(cx-170)}" y="160" width="340" height="280" rx="6" fill="none" stroke="{p["main2"]}" stroke-width="1.5" opacity="0.35"/>')
    elif kind == 2:  # close-up of one slice
        ct_slice(s, W * s.r.choice([0.42, 0.58]), s.r.uniform(320, 380), 1.9, tone, p["accent"], nodule=True)
        grid(s, p["main2"], step=60, op=0.07)
    else:  # empty scanner: gantry ring and table, no patient
        s.add(f'<rect width="{W}" height="{H}" fill="{s.lin(p["bg1"], p["bg2"])}"/>')
        cx = W * s.r.choice([0.5, 0.6, 0.4])
        bore = s.clip(f'<circle cx="{f(cx)}" cy="330" r="160"/>')
        s.add(f'<rect x="{f(cx-110)}" y="520" width="220" height="140" fill="{p["light"]}" opacity="0.55"/>'
              f'<circle cx="{f(cx+10)}" cy="345" r="275" fill="#000" opacity="0.15"/>'
              f'<circle cx="{f(cx)}" cy="330" r="270" fill="{p["light"]}"/>'
              f'<circle cx="{f(cx)}" cy="330" r="232" fill="none" stroke="#000" stroke-opacity="0.07" stroke-width="10"/>'
              f'<circle cx="{f(cx)}" cy="330" r="160" fill="{p["bg2"]}"/>'
              f'<g clip-path="{bore}"><circle cx="{f(cx)}" cy="330" r="120" fill="{p["dark"]}" opacity="0.5"/>'
              f'<rect x="{f(cx-170)}" y="392" width="340" height="26" rx="8" fill="{p["light"]}" opacity="0.92"/>'
              f'<rect x="{f(cx-170)}" y="418" width="340" height="80" fill="{p["dark"]}" opacity="0.35"/></g>'
              f'<circle cx="{f(cx)}" cy="330" r="160" fill="none" stroke="{p["accent"]}" stroke-width="6" filter="{s.glow(6)}"/>'
              f'<rect x="{f(cx-60)}" y="104" width="120" height="14" rx="7" fill="{p["accent"]}"/>'
              f'<circle cx="{f(cx+180)}" cy="180" r="8" fill="{p["main2"]}"/><circle cx="{f(cx+205)}" cy="200" r="8" fill="{p["accent"]}" opacity="0.7"/>')
    if kind != 3:
        s.grain(0.1)
        s.vignette(0.4)
    else:
        s.grain(0.05)


def t_pollution(s: Svg, v: int):
    p = s.p
    hues = [("#8e8576", "#c9b48f", "#5b5347"), ("#6f6b70", "#b9a58d", "#3e3a40"), ("#7d6a5a", "#d6a06a", "#45372c"),
            ("#5d6870", "#a7a99c", "#323a40"), ("#93806b", "#e0b77f", "#4d3f33"), ("#4e4a58", "#a48a86", "#2a2730")]
    top, bottom, city = hues[v % len(hues)]
    s.add(f'<rect width="{W}" height="{H}" fill="{s.lin(top, bottom)}"/>')
    sx = s.r.uniform(200, 1000)
    sy = s.r.uniform(110, 230)
    s.add(f'<circle cx="{f(sx)}" cy="{f(sy)}" r="{f(s.r.uniform(40, 70))}" fill="#f3d9a8" opacity="0.55" filter="{s.blur(6)}"/>')
    kind = v % 3
    base = H - s.r.uniform(60, 130)
    # far skyline in haze
    skyline(s, base - 40, 80, 220, city, x0=-20, wmin=30, wmax=80)
    s.add(f'<rect width="{W}" height="{H}" fill="{s.lin(bottom, bottom, stops=[(0, bottom, 0.0), (0.6, bottom, 0.55), (1, bottom, 0.2)])}"/>')
    chims = skyline(s, base, 60, 260 if kind != 1 else 160, city, windows="#f2c879", chimney=0.35 if kind == 1 else 0.12, wmin=50, wmax=120)
    if kind == 1 and not chims:
        chims = [(s.r.uniform(200, 1000), base - 280, 20)]
    for x, y, cw in chims:
        plume(s, x, y, "#d9d0c1", length=s.r.uniform(300, 600), width=cw * 0.8, drift=s.r.choice([-1, 1]), op=0.55)
    # haze bands
    for k in range(4):
        y = s.r.uniform(150, 520)
        s.add(f'<ellipse cx="{f(s.r.uniform(0, W))}" cy="{f(y)}" rx="{f(s.r.uniform(400, 800))}" ry="{f(s.r.uniform(30, 80))}" '
              f'fill="{bottom}" opacity="0.35" filter="{s.blur(30)}"/>')
    particles(s, 380, "#3b342c", rmin=0.8, rmax=3.2, opmin=0.15, opmax=0.5)
    if kind == 2:  # PM2.5 particle close-ups
        for _ in range(5):
            x, y, r = s.r.uniform(80, 1120), s.r.uniform(60, 360), s.r.uniform(10, 26)
            s.add(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" fill="#2e2822" opacity="0.55"/>'
                  f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r*2.1)}" fill="none" stroke="{p["accent"]}" stroke-width="2" opacity="0.7"/>')
    s.grain(0.09)


def t_city(s: Svg, v: int):
    pal = [("#bfe6ea", "#fbf3e3"), ("#cdeee0", "#fff7ea"), ("#a8d8ea", "#f4f8f2"), ("#ffd9bd", "#fff6ea"), ("#d7ecf7", "#f3fbf5")]
    top, bottom = pal[v % len(pal)]
    s.add(f'<rect width="{W}" height="{H}" fill="{s.lin(top, bottom)}"/>')
    sun(s, s.r.uniform(150, 1050), s.r.uniform(90, 170), s.r.uniform(34, 50), color="#F2A65A" if v % 2 else "#E4572E")
    for _ in range(3):
        cloud(s, s.r.uniform(50, 1150), s.r.uniform(60, 200), s.r.uniform(0.6, 1.3), "#ffffff", 0.85)
    base = H - s.r.uniform(110, 170)
    if v % 3 == 0:  # wind turbines on the horizon
        for _ in range(s.r.randint(3, 5)):
            x, h = s.r.uniform(60, 1140), s.r.uniform(160, 260)
            a = s.r.uniform(0, 120)
            blades = "".join(f'<line x1="0" y1="0" x2="0" y2="{-h*0.4:.0f}" transform="rotate({a + k*120:.0f})"/>' for k in range(3))
            s.add(f'<g stroke="#ffffff" stroke-linecap="round"><line x1="{f(x)}" y1="{f(base-40)}" x2="{f(x)}" y2="{f(base-40-h)}" stroke-width="5"/>'
                  f'<g transform="translate({f(x)},{f(base-40-h)})" stroke-width="6">{blades}</g></g>')
    skyline(s, base - 25, 90, 250, "#9ccfcf", wmin=40, wmax=90)
    skyline(s, base, 60, 190, "#0B6E73", windows="#d6ecED", wmin=50, wmax=120, win_op=0.7)
    hills(s, base + 30, 40, "#5E9E70", n=3)
    s.add(f'<rect x="0" y="{f(base+35)}" width="{W}" height="{H}" fill="#4B8A5E"/>')
    x = s.r.uniform(0, 40)
    while x < W:
        tree_round(s, x, base + 50 + s.r.uniform(0, 30), s.r.uniform(70, 120), "#2c4a33", s.r.choice(["#3E7D4F", "#6BAA5E", "#2F6B45"]), "#9CD08A")
        x += s.r.uniform(50, 140)
    # cycle lane
    s.add(f'<rect x="0" y="{H-46}" width="{W}" height="20" fill="#E4572E" opacity="0.85"/>'
          + "".join(f'<rect x="{x}" y="{H-37}" width="30" height="3" fill="#fff"/>' for x in range(10, W, 70)))
    s.grain(0.05)


def t_nature(s: Svg, v: int):
    p = s.p
    kind = v % 3
    if kind == 0:  # mountains + clean air
        top, mid = s.r.choice([("#bfe3ea", "#f6f1e3"), ("#d7ecf2", "#fdf3e6"), ("#c4e0d8", "#f3f6ee")])
        s.add(f'<rect width="{W}" height="{H}" fill="{s.lin(top, mid)}"/>')
        sun(s, s.r.uniform(200, 1000), 150, 40, color="#F2A65A")
        for i, (c, b, hm) in enumerate([("#9fc7c4", 380, 220), ("#6aa3a0", 450, 200), ("#3f7f7d", 520, 160), ("#22553A", 600, 120)]):
            mountains(s, b, hm, c)
            if i >= 2:
                x = s.r.uniform(-20, 20)
                while x < W:
                    tree_pine(s, x, b + 30 + s.r.uniform(-10, 20), s.r.uniform(40, 90), c if i == 2 else "#173b28")
                    x += s.r.uniform(18, 50)
        wave_lines(s, 3, "#ffffff", 120, 230, amp=14, op=0.7, sw=3)
    elif kind == 1:  # tree shaped like lungs (bronchial tree as a trunk)
        s.background()
        cx = side(s)
        tips: list = []
        s.add(bronchial_tree(s, cx, 330, 560, p["main"], width=1.6, depth=6, tips=tips, opacity=0.95).replace(
            "<g ", f'<g transform="rotate(180 {f(cx)} 330)" ', 1))
        leaves = []
        for x, y in tips:
            x2, y2 = 2 * cx - x, 660 - y
            for _ in range(3):
                leaves.append(f'<circle cx="{f(x2 + s.r.uniform(-14, 14))}" cy="{f(y2 + s.r.uniform(-14, 14))}" r="{f(s.r.uniform(6, 14))}" '
                              f'fill="{s.r.choice([p["main2"], p["main"], p["accent"]])}" opacity="{s.r.uniform(.55, .95):.2f}"/>')
        s.add("".join(leaves))
    else:  # leaves pattern
        s.background()
        for _ in range(26):
            x, y, ln = s.r.uniform(-40, W + 40), s.r.uniform(-40, H + 40), s.r.uniform(60, 150)
            a = s.r.uniform(0, 360)
            c = s.r.choice([p["main"], p["main2"], p["main2"], p["accent"]])
            s.add(f'<g transform="translate({f(x)},{f(y)}) rotate({f(a)})" opacity="{s.r.uniform(.5,.95):.2f}">'
                  f'<path d="M 0 0 Q {f(ln*0.5)} {f(-ln*0.32)} {f(ln)} 0 Q {f(ln*0.5)} {f(ln*0.32)} 0 0 Z" fill="{c}"/>'
                  f'<line x1="0" y1="0" x2="{f(ln*0.9)}" y2="0" stroke="{p["bg2"]}" stroke-width="2" opacity="0.5"/></g>')
    s.grain(0.05)


def t_airways(s: Svg, v: int):
    """Alveoli, airway cross-sections, breath flow — COPD / bronchiectasis / chronic disease."""
    p = s.p
    s.background()
    kind = v % 3
    if kind == 0:  # airway tree ending in alveolar clusters
        cx = side(s)
        tips: list = []
        sz = s.r.uniform(520, 700)
        s.add(bronchial_tree(s, cx, 330, sz, p["main2"], width=1.4, depth=4, tips=tips, opacity=1))
        for x, y in tips:
            sc = s.r.uniform(0.45, 0.75)
            for _ in range(9):
                a, d = s.r.uniform(0, 6.28), s.r.uniform(0, 30 * sc)
                rr = s.r.uniform(16, 24) * sc
                bx, by = x + math.cos(a) * d, y + math.sin(a) * d + 8 * sc
                s.add(f'<circle cx="{f(bx)}" cy="{f(by)}" r="{f(rr)}" fill="{p["main"]}" stroke="{p["bg2"]}" stroke-width="1.5"/>'
                      f'<circle cx="{f(bx - rr*0.3)}" cy="{f(by - rr*0.3)}" r="{f(rr*0.32)}" fill="{p["light"]}" opacity="0.4"/>')
            s.add(f'<path d="M {f(x-30*sc)} {f(y+10)} q {f(30*sc)} {f(40*sc)} {f(60*sc)} 0" fill="none" stroke="{p["accent"]}" stroke-width="2.5" opacity="0.85"/>')
    elif kind == 1:  # airway cross-sections: healthy vs narrowed
        xs = [W * 0.32, W * 0.68] if s.r.random() < 0.6 else [W * 0.5]
        for i, cx in enumerate(xs):
            R = 190 if len(xs) == 2 else 230
            narrowed = (i == 1) or (len(xs) == 1 and v % 2)
            wall = R * (0.42 if narrowed else 0.2)
            s.add(f'<circle cx="{f(cx)}" cy="300" r="{f(R)}" fill="{p["main2"]}"/>')
            s.add(f'<circle cx="{f(cx)}" cy="300" r="{f(R - 10)}" fill="{p["accent"] if narrowed else p["main"]}" opacity="{0.55 if narrowed else 0.35}"/>')
            # folded mucosa
            pts = []
            lumen = R - wall
            for k in range(0, 73):
                a = k / 72 * 6.28
                rr = lumen + (wall * 0.18 if narrowed else 4) * math.sin(a * (14 if narrowed else 20))
                pts.append(f"{f(cx + math.cos(a)*rr)},{f(300 + math.sin(a)*rr)}")
            s.add(f'<polygon points="{" ".join(pts)}" fill="{p["bg2"]}" stroke="{p["light"]}" stroke-width="3"/>')
            if narrowed:
                particles(s, 30, p["main"], cx - lumen * 0.6, 300 - lumen * 0.6, cx + lumen * 0.6, 300 + lumen * 0.6, 2, 6, opmin=.4, opmax=.9)
    else:  # breath flow
        cx = side(s)
        lungs(s, cx, 315, 420, "outline", line=p["main"], tree=False)
        for k in range(14):
            y0 = s.r.uniform(40, 600)
            amp = s.r.uniform(20, 70)
            s.add(f'<path d="M -20 {f(y0)} C 300 {f(y0-amp)}, {f(cx-200)} {f(y0+amp)}, {f(cx)} {f(315 + (y0-315)*0.25)} '
                  f'S {W-200} {f(y0-amp)}, {W+20} {f(y0)}" fill="none" stroke="{s.r.choice([p["main2"], p["main"], p["accent"]])}" '
                  f'stroke-width="{s.r.uniform(1.5, 4):.1f}" opacity="{s.r.uniform(.25,.7):.2f}" stroke-linecap="round"/>')
    s.grain(0.06)


def inhaler(s: Svg, x, y, sc, body, can, ang=0, mirror=False):
    sx = -sc if mirror else sc
    s.add(f'<g transform="translate({f(x)},{f(y)}) rotate({f(ang)}) scale({sx:.2f},{sc:.2f})">'
          f'<rect x="-48" y="-260" width="96" height="130" rx="20" fill="{can}"/>'
          f'<rect x="-48" y="-200" width="96" height="10" fill="#000" opacity="0.12"/>'
          f'<path d="M -62 -150 h 124 v 210 q 0 20 -20 20 h -10 v 0 H -62 Z" fill="{body}"/>'
          f'<path d="M -62 40 h -110 q -18 0 -18 18 v 4 q 0 18 18 18 h 152 v -40 Z" fill="{body}"/>'
          f'<rect x="-188" y="44" width="16" height="32" rx="4" fill="#000" opacity="0.18"/>'
          f'<rect x="-50" y="-140" width="18" height="180" rx="9" fill="#fff" opacity="0.18"/></g>')


def t_asthma(s: Svg, v: int):
    p = s.p
    s.background()
    kind = v % 4
    if kind == 0:  # MDI inhaler with mist leaving the mouthpiece
        x = s.r.choice([W * 0.66, W * 0.74, W * 0.34, W * 0.26])
        mirror = x < W / 2
        inhaler(s, x, 300, 1.25, p["main"], p["accent"], ang=s.r.uniform(-6, 6), mirror=mirror)
        mouth = x + (235 if mirror else -235)
        direction = 1 if mirror else -1
        for k in range(170):
            d = s.r.uniform(0, 420)
            spread = 6 + d * 0.42
            s.add(f'<circle cx="{f(mouth + direction * d)}" cy="{f(386 + s.r.uniform(-spread, spread))}" r="{f(s.r.uniform(1.5, 6))}" '
                  f'fill="{p["light"]}" opacity="{max(0.05, 0.8 - d/520):.2f}"/>')
    elif kind == 1:  # spacer device
        cx = side(s)
        s.add(f'<g transform="translate({f(cx)},300) rotate({f(s.r.uniform(-10, 10))})">'
              f'<path d="M -320 -40 L -120 -110 Q 0 -130 120 -110 L 260 -60 v 120 L 120 110 Q 0 130 -120 110 L -320 40 Z" fill="{p["main"]}" opacity="0.35"/>'
              f'<path d="M -320 -40 L -120 -110 Q 0 -130 120 -110 L 260 -60 v 120 L 120 110 Q 0 130 -120 110 L -320 40 Z" fill="none" stroke="{p["main"]}" stroke-width="5"/>'
              f'<rect x="-400" y="-28" width="90" height="56" rx="12" fill="{p["main2"]}"/>'
              f'<rect x="250" y="-70" width="40" height="140" rx="12" fill="{p["accent"]}"/></g>')
        particles(s, 120, p["light"], cx - 260, 230, cx + 220, 370, 1.5, 4.5, opmin=.2, opmax=.7)
    elif kind == 2:  # peak-flow meter with breath waves
        wave_lines(s, 7, p["main"], 120, 520, amp=40, op=0.25, sw=3)
        cx = side(s)
        s.add(f'<g transform="translate({f(cx)},315) rotate({f(s.r.uniform(-12, -4))})">'
              f'<rect x="-330" y="-55" width="560" height="110" rx="55" fill="{p["light"]}"/>'
              f'<rect x="230" y="-38" width="110" height="76" rx="20" fill="{p["main2"]}"/>'
              + "".join(f'<line x1="{x}" y1="-40" x2="{x}" y2="{-10 if x % 80 else 5}" stroke="{p["dark"]}" stroke-width="3"/>' for x in range(-280, 200, 20))
              + f'<rect x="{s.r.randint(-100, 120)}" y="-48" width="16" height="96" rx="6" fill="{p["accent"]}"/></g>')
    else:  # dry-powder disc inhaler
        cx = side(s)
        s.add(f'<circle cx="{f(cx+10)}" cy="325" r="190" fill="#000" opacity="0.18"/>'
              f'<circle cx="{f(cx)}" cy="315" r="190" fill="{p["main"]}"/>'
              f'<circle cx="{f(cx)}" cy="315" r="150" fill="{p["main2"]}"/>'
              f'<path d="M {f(cx-150)} 315 A 150 150 0 0 1 {f(cx+150)} 315 Z" fill="{p["light"]}" opacity="0.25"/>'
              f'<rect x="{f(cx-40)}" y="95" width="80" height="60" rx="16" fill="{p["accent"]}"/>'
              f'<circle cx="{f(cx)}" cy="315" r="44" fill="{p["light"]}"/>'
              f'<text x="{f(cx)}" y="330" font-family="Inter, sans-serif" font-size="40" font-weight="700" text-anchor="middle" fill="{p["dark"]}">{s.r.randint(10, 60)}</text>')
        particles(s, 60, p["light"], 0, 0, W, H, 2, 6, opmin=.1, opmax=.35)
    s.grain(0.05)


def t_pharma(s: Svg, v: int):
    p = s.p
    s.background()
    kind = v % 4
    cols = [p["accent"], p["main"], p["main2"], p["light"]]
    if kind == 0:  # scattered capsules + tablets
        for _ in range(s.r.randint(14, 22)):
            x, y = s.r.uniform(-20, W + 20), s.r.uniform(-20, H + 20)
            if s.r.random() < 0.6:
                c1, c2 = s.r.sample(cols, 2)
                capsule(s, x, y, s.r.uniform(110, 170), s.r.uniform(42, 60), s.r.uniform(0, 180), c1, c2)
            else:
                tablet(s, x, y, s.r.uniform(26, 44), s.r.choice(cols), s.r.uniform(0, 180))
    elif kind == 1:  # molecule hero
        hex_pattern(s, p["main"], size=36, op=0.07)
        cx = side(s)
        molecule(s, cx, 290, 70, p["main"], atom=p["accent"], rings=s.r.randint(2, 4), sw=6)
        molecule(s, s.r.uniform(100, 1100), s.r.uniform(60, 560), 34, p["main2"], rings=2, sw=3, op=0.6)
    elif kind == 2:  # blister pack
        cx = side(s)
        ang = s.r.uniform(-14, 14)
        cells = "".join(
            f'<circle cx="{-150 + i*100}" cy="{-100 + j*100}" r="36" fill="{p["light"]}" opacity="0.35"/>'
            + (f'<circle cx="{-150 + i*100}" cy="{-100 + j*100}" r="27" fill="{p["accent"] if (i+j) % 3 == 0 else p["light"]}"/>' if s.r.random() < 0.8 else
               f'<circle cx="{-150 + i*100}" cy="{-100 + j*100}" r="30" fill="none" stroke="{p["dark"]}" stroke-width="2" opacity="0.4"/>')
            for i in range(4) for j in range(3))
        s.add(f'<g transform="translate({f(cx)},300) rotate({f(ang)})"><rect x="-224" y="-174" width="448" height="348" rx="26" fill="#000" opacity="0.18" transform="translate(10,14)"/>'
              f'<rect x="-224" y="-174" width="448" height="348" rx="26" fill="{p["main2"]}"/>{cells}</g>')
    else:  # vials row
        n = s.r.randint(3, 5)
        x0 = s.r.uniform(120, 300)
        for i in range(n):
            x = x0 + i * 170
            h = s.r.uniform(230, 300)
            lvl = s.r.uniform(0.35, 0.75)
            y = 520 - h
            s.add(f'<rect x="{f(x)}" y="{f(y)}" width="110" height="{f(h)}" rx="18" fill="{p["light"]}" opacity="0.25"/>'
                  f'<rect x="{f(x)}" y="{f(y + h*(1-lvl))}" width="110" height="{f(h*lvl)}" rx="18" fill="{s.r.choice([p["accent"], p["main"], p["main2"]])}" opacity="0.85"/>'
                  f'<rect x="{f(x-6)}" y="{f(y-46)}" width="122" height="56" rx="10" fill="{p["dark"]}"/>'
                  f'<rect x="{f(x+12)}" y="{f(y+20)}" width="14" height="{f(h-50)}" rx="7" fill="#fff" opacity="0.3"/>')
        s.add(f'<rect x="0" y="520" width="{W}" height="12" fill="{p["dark"]}" opacity="0.5"/>')
    s.grain(0.05)


def t_research(s: Svg, v: int):
    p = s.p
    s.background()
    kind = v % 4
    if kind == 0:  # DNA helix
        cx0 = s.r.choice([0, 150, -150])
        ang = s.r.uniform(-25, 25)
        g = [f'<g transform="rotate({f(ang)} 600 315) translate({cx0},0)">']
        for k in range(0, 48):
            x = -100 + k * 30
            a = k * 0.42
            y1, y2 = 315 + 120 * math.sin(a), 315 - 120 * math.sin(a)
            depth = math.cos(a)
            g.append(f'<line x1="{x}" y1="{f(y1)}" x2="{x}" y2="{f(y2)}" stroke="{p["main2"]}" stroke-width="5" opacity="0.6"/>')
            g.append(f'<circle cx="{x}" cy="{f(y1)}" r="{f(10 + 4*depth)}" fill="{p["main"]}"/>'
                     f'<circle cx="{x}" cy="{f(y2)}" r="{f(10 - 4*depth)}" fill="{p["accent"]}"/>')
        g.append("</g>")
        s.add("".join(g))
    elif kind == 1:  # petri dishes
        for _ in range(s.r.randint(2, 4)):
            cx, cy, r = s.r.uniform(150, 1050), s.r.uniform(120, 480), s.r.uniform(110, 190)
            s.add(f'<circle cx="{f(cx+8)}" cy="{f(cy+12)}" r="{f(r)}" fill="#000" opacity="0.18"/>'
                  f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="{p["light"]}" opacity="0.22" stroke="{p["light"]}" stroke-width="6"/>'
                  f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r*0.9)}" fill="{p["main2"]}" opacity="0.35"/>')
            for _ in range(s.r.randint(8, 22)):
                a, d = s.r.uniform(0, 6.28), s.r.uniform(0, r * 0.8)
                s.add(f'<circle cx="{f(cx + math.cos(a)*d)}" cy="{f(cy + math.sin(a)*d)}" r="{f(s.r.uniform(4, 16))}" '
                      f'fill="{s.r.choice([p["accent"], p["main"], p["light"]])}" opacity="{s.r.uniform(.5,.9):.2f}"/>')
    elif kind == 2:  # test tubes in rack
        n = s.r.randint(5, 7)
        x0 = s.r.uniform(160, 380)
        for i in range(n):
            x = x0 + i * 100
            lvl = s.r.uniform(0.25, 0.8)
            s.add(f'<rect x="{f(x)}" y="110" width="56" height="360" rx="28" fill="{p["light"]}" opacity="0.22"/>'
                  f'<rect x="{f(x)}" y="{f(110 + 360*(1-lvl))}" width="56" height="{f(360*lvl)}" rx="28" fill="{s.r.choice([p["accent"], p["main"], p["main2"]])}"/>'
                  f'<rect x="{f(x+10)}" y="130" width="10" height="300" rx="5" fill="#fff" opacity="0.3"/>')
        s.add(f'<rect x="{f(x0 - 60)}" y="330" width="{f(n*100 + 80)}" height="40" rx="8" fill="{p["dark"]}" opacity="0.85"/>'
              f'<rect x="{f(x0 - 60)}" y="470" width="{f(n*100 + 80)}" height="30" rx="8" fill="{p["dark"]}" opacity="0.85"/>')
    else:  # magnifier over data
        dot_grid(s, p["main"], op=0.25)
        cx, cy = side(s), 290
        pts = []
        y = 420
        for k in range(13):
            y = max(120, min(520, y + s.r.uniform(-60, 40)))
            pts.append((40 + k * 95, y))
        s.add(f'<polyline points="{" ".join(f"{f(a)},{f(b)}" for a, b in pts)}" fill="none" stroke="{p["main"]}" stroke-width="4" opacity="0.6"/>')
        s.add(f'<circle cx="{f(cx)}" cy="{cy}" r="150" fill="{p["light"]}" opacity="0.14"/>'
              f'<circle cx="{f(cx)}" cy="{cy}" r="150" fill="none" stroke="{p["light"]}" stroke-width="18"/>'
              f'<line x1="{f(cx+106)}" y1="{cy+106}" x2="{f(cx+250)}" y2="{cy+250}" stroke="{p["accent"]}" stroke-width="36" stroke-linecap="round"/>')
        for a, b in pts:
            if (a - cx) ** 2 + (b - cy) ** 2 < 140 ** 2:
                s.add(f'<circle cx="{f(a)}" cy="{f(b)}" r="9" fill="{p["accent"]}"/>')
    s.grain(0.05)


def t_cells(s: Svg, v: int):
    kind = v % 3
    if kind == 0:  # H&E-like stain
        bg, cyto, nuc, rbc = "#f4dbe4", "#e7a9c3", "#4b2a72", "#d9536f"
    elif kind == 1:  # fluorescence
        bg, cyto, nuc, rbc = "#05070d", "#1fa57a", "#3c6cf0", "#e0408a"
    else:  # teal brand micro
        bg, cyto, nuc, rbc = "#0b3a3e", "#5fb8b8", "#d6eced", "#E4572E"
    s.add(f'<rect width="{W}" height="{H}" fill="{bg}"/>')
    # blurred background cells
    for _ in range(40):
        x, y, r = s.r.uniform(0, W), s.r.uniform(0, H), s.r.uniform(20, 50)
        s.add(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" fill="{cyto}" opacity="0.35" filter="{s.blur(8)}"/>')
    # fibres
    for _ in range(12 if kind != 1 else 6):
        y0 = s.r.uniform(0, H)
        s.add(f'<path d="M -20 {f(y0)} C 300 {f(y0 + s.r.uniform(-120,120))}, 800 {f(y0 + s.r.uniform(-120,120))}, {W+20} {f(y0 + s.r.uniform(-80,80))}" '
              f'fill="none" stroke="{cyto}" stroke-width="{s.r.uniform(2, 7):.1f}" opacity="0.45"/>')
    gl = s.glow(6) if kind == 1 else None
    for _ in range(s.r.randint(14, 22)):
        x, y, r = s.r.uniform(40, W - 40), s.r.uniform(40, H - 40), s.r.uniform(34, 60)
        if s.r.random() < 0.35:  # red cell disc
            s.add(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r*0.8)}" fill="{rbc}" opacity="0.85"/>'
                  f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r*0.35)}" fill="{bg}" opacity="0.35"/>')
            continue
        lobes = "".join(f'<circle cx="{f(x + math.cos(a)*r*0.32)}" cy="{f(y + math.sin(a)*r*0.32)}" r="{f(r*s.r.uniform(.24,.32))}"/>'
                        for a in [s.r.uniform(0, 6.28) for _ in range(s.r.randint(2, 4))])
        flt = f' filter="{gl}"' if gl else ""
        s.add(f'<g{flt}><circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" fill="{cyto}" opacity="0.8"/>'
              f'<g fill="{nuc}" opacity="0.92">{lobes}</g></g>')
    # bacteria rods
    if v % 2:
        for _ in range(16):
            x, y, a = s.r.uniform(0, W), s.r.uniform(0, H), s.r.uniform(0, 180)
            s.add(f'<rect x="{f(x)}" y="{f(y)}" width="34" height="11" rx="5.5" fill="{nuc}" opacity="0.8" transform="rotate({f(a)} {f(x)} {f(y)})"/>')
    if kind != 1:
        s.vignette(0.3)
    s.grain(0.06)


def cigarette(s: Svg, x, y, ln, ang, broken=False, p=None):
    p = p or s.p
    w = ln * 0.085
    g = f'<g transform="translate({f(x)},{f(y)}) rotate({f(ang)})">'
    if not broken:
        g += f'<rect x="{f(-ln/2+8)}" y="{f(-w/2+12)}" width="{f(ln)}" height="{f(w)}" rx="{f(w*0.3)}" fill="#000" opacity="0.2"/>'
    if broken:
        g += (f'<g transform="rotate(-14 {f(-ln*0.02)} 0)"><rect x="{f(-ln/2)}" y="{f(-w/2)}" width="{f(ln*0.47)}" height="{f(w)}" fill="#f7f3ec"/>'
              f'<rect x="{f(-ln/2)}" y="{f(-w/2)}" width="{f(ln*0.24)}" height="{f(w)}" fill="#E39A4C"/></g>'
              f'<g transform="rotate(12 {f(ln*0.05)} 0)"><rect x="{f(ln*0.05)}" y="{f(-w/2)}" width="{f(ln*0.45)}" height="{f(w)}" fill="#f7f3ec"/>'
              f'<rect x="{f(ln*0.47)}" y="{f(-w/2)}" width="{f(ln*0.03)}" height="{f(w)}" fill="#7a7a7a"/></g>')
    else:
        g += (f'<rect x="{f(-ln/2)}" y="{f(-w/2)}" width="{f(ln)}" height="{f(w)}" fill="#f7f3ec"/>'
              f'<rect x="{f(-ln/2)}" y="{f(-w/2)}" width="{f(ln*0.24)}" height="{f(w)}" fill="#E39A4C"/>'
              f'<rect x="{f(ln/2 - ln*0.05)}" y="{f(-w/2)}" width="{f(ln*0.05)}" height="{f(w)}" fill="#8a8a8a"/>'
              f'<rect x="{f(ln/2 - ln*0.012)}" y="{f(-w/2)}" width="{f(ln*0.012)}" height="{f(w)}" fill="{p["accent"]}"/>')
    g += "</g>"
    s.add(g)


def smoke(s: Svg, x, y, color, height=380, op=0.5, sw=6):
    for k in range(5):
        pts = []
        ph = s.r.uniform(0, 6)
        for i in range(0, 41):
            t = i / 40
            pts.append(f"{f(x + (30 + 60 * t) * math.sin(ph + t * 9) + k * 6)},{f(y - height * t)}")
        s.add(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="round" '
              f'opacity="{op * (1 - k*0.12):.2f}" filter="{s.blur(3 + sw*0.4)}"/>')


def t_tobacco(s: Svg, v: int):
    p = s.p
    s.background()
    kind = v % 4
    if kind == 0:  # broken cigarette
        cx = side(s)
        cigarette(s, cx, 330, 620, s.r.uniform(-12, 12), broken=True)
    elif kind == 1:  # smoke-free sign
        cx = side(s)
        cigarette(s, cx, 315, 380, 0)
        smoke(s, cx + 180, 300, p["light"], height=220, op=0.4, sw=4)
        s.add(f'<circle cx="{f(cx)}" cy="315" r="230" fill="none" stroke="{p["accent"]}" stroke-width="36"/>'
              f'<line x1="{f(cx-163)}" y1="152" x2="{f(cx+163)}" y2="478" stroke="{p["accent"]}" stroke-width="36"/>')
    elif kind == 2:  # generic vape device + vapour cloud
        cx = side(s)
        ang = s.r.uniform(-30, -10)
        s.add(f'<g transform="translate({f(cx)},380) rotate({f(ang)})">'
              f'<rect x="-260" y="-48" width="420" height="96" rx="30" fill="{p["dark"]}"/>'
              f'<rect x="150" y="-34" width="110" height="68" rx="18" fill="{p["main2"]}"/>'
              f'<rect x="-200" y="-14" width="70" height="28" rx="10" fill="{p["accent"]}" opacity="0.9"/>'
              f'<rect x="-240" y="-36" width="380" height="14" rx="7" fill="#fff" opacity="0.15"/></g>')
        for _ in range(26):
            s.add(f'<circle cx="{f(cx + s.r.uniform(150, 420))}" cy="{f(s.r.uniform(80, 300))}" r="{f(s.r.uniform(30, 90))}" '
                  f'fill="{p["light"]}" opacity="0.18" filter="{s.blur(18)}"/>')
    else:  # lungs with smoke
        cx = side(s)
        lungs(s, cx, 320, 470, "soft", fill=p["main2"], tree=True, opacity=0.9)
        for _ in range(3):
            smoke(s, cx + s.r.uniform(-120, 120), 640, "#4a4642", height=560, op=0.35, sw=22)
    s.grain(0.06)


def t_data(s: Svg, v: int):
    p = s.p
    s.background()
    kind = v % 4
    grid(s, p["main"], step=40, op=0.08)
    if kind == 0:  # flow-volume loops (spirometry)
        x0, y0 = s.r.uniform(160, 400), 470
        for k, (pk, colr, op) in enumerate([(1.0, p["main"], 0.9), (s.r.uniform(0.45, 0.7), p["accent"], 1.0)]):
            ln = 640 * (1 if k == 0 else s.r.uniform(0.7, 0.9))
            pts = [(x0, y0)]
            for i in range(1, 61):
                t = i / 60
                if t < 0.12:
                    y = y0 - 360 * pk * (t / 0.12) ** 0.7
                else:
                    tt = (t - 0.12) / 0.88
                    y = y0 - 360 * pk * (1 - tt) ** (1.0 if k == 0 else 2.2)
                pts.append((x0 + ln * t, y))
            pts2 = [(x0 + ln * (1 - i / 40), y0 + 120 * pk * math.sin(math.pi * i / 40)) for i in range(41)]
            s.add(f'<polyline points="{" ".join(f"{f(a)},{f(b)}" for a, b in pts + pts2)}" fill="none" stroke="{colr}" '
                  f'stroke-width="6" stroke-linejoin="round" opacity="{op}"/>')
        s.add(f'<line x1="{f(x0-20)}" y1="{y0}" x2="{f(x0+720)}" y2="{y0}" stroke="{p["light"]}" stroke-width="2" opacity="0.6"/>'
              f'<line x1="{f(x0)}" y1="60" x2="{f(x0)}" y2="610" stroke="{p["light"]}" stroke-width="2" opacity="0.6"/>')
    elif kind == 1:  # bars
        n = s.r.randint(7, 11)
        bw = 760 / n
        x0 = s.r.uniform(80, 360)
        hi = s.r.randrange(n)
        for i in range(n):
            h = s.r.uniform(90, 420)
            s.add(f'<rect x="{f(x0 + i*bw)}" y="{f(560-h)}" width="{f(bw*0.66)}" height="{f(h)}" rx="6" '
                  f'fill="{p["accent"] if i == hi else p["main2"]}" opacity="{1 if i == hi else 0.8}"/>')
        s.add(f'<line x1="{f(x0-20)}" y1="560" x2="{f(x0+780)}" y2="560" stroke="{p["light"]}" stroke-width="3"/>')
    elif kind == 2:  # pulse across the frame + lungs mark faint
        lungs(s, side(s), 320, 520, "outline", line=p["main2"], tree=False, opacity=0.5)
        pulse_line(s, s.r.uniform(260, 360), p["accent"], beats=s.r.randint(2, 4), amp=150, sw=7)
    else:  # trend lines
        for k in range(3):
            y = s.r.uniform(250, 480)
            pts = []
            for i in range(14):
                y = max(80, min(560, y + s.r.uniform(-50, 30 if k == 0 else 50)))
                pts.append((40 + i * 86, y))
            colr = [p["accent"], p["main"], p["main2"]][k]
            s.add(f'<polyline points="{" ".join(f"{f(a)},{f(b)}" for a, b in pts)}" fill="none" stroke="{colr}" stroke-width="{6 - k}" '
                  f'stroke-linejoin="round" opacity="{1 - k*0.25}"/>')
            for a, b in pts[::3]:
                s.add(f'<circle cx="{f(a)}" cy="{f(b)}" r="{7 - k}" fill="{colr}"/>')
    s.grain(0.05)


def globe(s: Svg, cx, cy, r, color, dots=True, accent=None):
    p = s.p
    out = [f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="{color}" opacity="0.12"/>',
           f'<g fill="none" stroke="{color}" stroke-width="2" opacity="0.6"><circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}"/>']
    for k in range(1, 6):
        out.append(f'<ellipse cx="{f(cx)}" cy="{f(cy)}" rx="{f(r * k / 6)}" ry="{f(r)}"/>')
    for k in range(-2, 3):
        yy = cy + r * k / 3
        rx = math.sqrt(max(r * r - (yy - cy) ** 2, 0))
        out.append(f'<ellipse cx="{f(cx)}" cy="{f(yy)}" rx="{f(rx)}" ry="{f(rx*0.12)}"/>')
    out.append("</g>")
    s.add("".join(out))
    nodes = []
    if dots:
        for _ in range(14):
            a, d = s.r.uniform(0, 6.28), r * math.sqrt(s.r.uniform(0, 0.85))
            nodes.append((cx + math.cos(a) * d, cy + math.sin(a) * d))
        arcs = []
        for _ in range(10):
            (x1, y1), (x2, y2) = s.r.sample(nodes, 2)
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - abs(x2 - x1) * 0.4
            arcs.append(f'<path d="M {f(x1)} {f(y1)} Q {f(mx)} {f(my)} {f(x2)} {f(y2)}"/>')
        s.add(f'<g fill="none" stroke="{accent or p["accent"]}" stroke-width="2.5" opacity="0.8">{"".join(arcs)}</g>')
        s.add("".join(f'<circle cx="{f(x)}" cy="{f(y)}" r="6" fill="{accent or p["accent"]}"/>' for x, y in nodes))


def t_globe(s: Svg, v: int):
    p = s.p
    s.background()
    kind = v % 4
    if kind == 0:
        globe(s, side(s), 330, 270, p["main"])
    elif kind == 1:  # big cropped globe
        globe(s, s.r.choice([180, 1020]), 520, 520, p["main"])
        particles(s, 80, p["main"], rmin=1, rmax=2.5, opmin=.2, opmax=.6)
    elif kind == 2:  # documents / policy papers with a seal
        cx = side(s)
        for k in range(3):
            ang = s.r.uniform(-12, 12)
            lines = "".join(f'<rect x="-150" y="{-150 + i*34}" width="{s.r.randint(160, 300)}" height="10" rx="5" fill="{p["dark"]}" opacity="0.25"/>' for i in range(9))
            s.add(f'<g transform="translate({f(cx + (k-1)*40)},{f(320 + (k-1)*10)}) rotate({f(ang)})">'
                  f'<rect x="-190" y="-230" width="380" height="460" rx="10" fill="#000" opacity="0.15" transform="translate(10,14)"/>'
                  f'<rect x="-190" y="-230" width="380" height="460" rx="10" fill="{p["light"]}"/>'
                  f'<rect x="-150" y="-190" width="200" height="22" rx="6" fill="{p["main2"] if p["dk"] else p["main"]}"/>{lines}</g>')
        s.add(f'<circle cx="{f(cx+120)}" cy="440" r="56" fill="{p["accent"]}"/><circle cx="{f(cx+120)}" cy="440" r="42" fill="none" stroke="#fff" stroke-width="3" stroke-dasharray="6 5"/>')
    else:  # civic building facade (generic columns)
        cx = side(s)
        cols = "".join(f'<rect x="{f(cx - 250 + i*84)}" y="250" width="40" height="230" fill="{p["light"]}"/>' for i in range(7))
        s.add(f'<polygon points="{f(cx-300)},240 {f(cx)},120 {f(cx+300)},240" fill="{p["light"]}"/>'
              f'<rect x="{f(cx-300)}" y="236" width="600" height="20" fill="{p["light"]}"/>{cols}'
              f'<rect x="{f(cx-320)}" y="480" width="640" height="24" fill="{p["light"]}"/><rect x="{f(cx-350)}" y="504" width="700" height="24" fill="{p["light"]}" opacity="0.8"/>'
              f'<circle cx="{f(cx)}" cy="196" r="22" fill="{p["accent"]}"/>')
    s.grain(0.05)


def t_events(s: Svg, v: int):
    p = s.p
    s.background((0, 0, 0, 1))
    kind = v % 3
    if kind == 0:  # stage + screen + seat rows (seen from behind, empty)
        cx = s.r.choice([W * 0.5, W * 0.6, W * 0.42])
        for k in range(3):
            a = s.r.uniform(-25, 25)
            s.add(f'<polygon points="{f(cx + a*8)},0 {f(cx - 260 + a*4)},{H} {f(cx + 260 + a*4)},{H}" fill="{p["light"]}" opacity="0.06"/>')
        s.add(f'<rect x="{f(cx-300)}" y="70" width="600" height="300" rx="10" fill="{p["dark"]}"/>'
              f'<rect x="{f(cx-285)}" y="85" width="570" height="270" rx="6" fill="{s.lin(p["bg1"], p["accent"], 0, 0, 1, 1)}"/>')
        lungs(s, cx, 220, 220, "filled", fill=p["light"], tree=False, opacity=0.85)
        s.add(f'<rect x="{f(cx+170)}" y="300" width="70" height="120" rx="6" fill="{p["dark"]}"/><rect x="0" y="410" width="{W}" height="20" fill="{p["dark"]}"/>')
        for row in range(4):
            y = 455 + row * 50
            for i in range(-1, 20):
                x = i * 70 + (35 if row % 2 else 0)
                s.add(f'<rect x="{x}" y="{y}" width="58" height="60" rx="14" fill="{p["dark"]}" opacity="{0.75 + row*0.06:.2f}"/>')
    elif kind == 1:  # calendar
        cx = side(s)
        s.add(f'<g transform="translate({f(cx)},320) rotate({f(s.r.uniform(-6, 6))})">'
              f'<rect x="-260" y="-220" width="520" height="460" rx="24" fill="#000" opacity="0.18" transform="translate(10,14)"/>'
              f'<rect x="-260" y="-220" width="520" height="460" rx="24" fill="{p["light"]}"/>'
              f'<rect x="-260" y="-220" width="520" height="90" rx="24" fill="{p["accent"]}"/><rect x="-260" y="-150" width="520" height="20" fill="{p["accent"]}"/>'
              + "".join(f'<rect x="{-150 + i*240}" y="-250" width="20" height="70" rx="10" fill="{p["dark"]}"/>' for i in range(2)))
        hi = (s.r.randrange(7), s.r.randrange(5))
        cells = []
        for j in range(5):
            for i in range(7):
                x, y = -230 + i * 66, -110 + j * 66
                if (i, j) == hi:
                    cells.append(f'<circle cx="{x+26}" cy="{y+26}" r="28" fill="{p["accent"]}"/>')
                else:
                    cells.append(f'<rect x="{x+8}" y="{y+8}" width="38" height="38" rx="8" fill="{p["main2"] if p["dk"] else p["main"]}" opacity="0.35"/>')
        s.add("".join(cells) + "</g>")
    else:  # lanyard badges
        for k in range(s.r.randint(2, 3)):
            x = s.r.uniform(200, 1000)
            ang = s.r.uniform(-15, 15)
            s.add(f'<g transform="translate({f(x)},{f(s.r.uniform(260, 340))}) rotate({f(ang)})">'
                  f'<path d="M -60 -380 L -20 -150 M 60 -380 L 20 -150" stroke="{p["accent"]}" stroke-width="16" fill="none"/>'
                  f'<rect x="-120" y="-150" width="240" height="320" rx="18" fill="{p["light"]}"/>'
                  f'<rect x="-120" y="-150" width="240" height="70" rx="18" fill="{p["main2"] if p["dk"] else p["main"]}"/>'
                  f'<rect x="-30" y="-170" width="60" height="30" rx="8" fill="{p["dark"]}"/>'
                  f'<rect x="-80" y="0" width="160" height="16" rx="8" fill="{p["dark"]}" opacity="0.35"/>'
                  f'<rect x="-60" y="34" width="120" height="12" rx="6" fill="{p["dark"]}" opacity="0.2"/>'
                  f'<rect x="-80" y="90" width="160" height="40" rx="8" fill="{p["accent"]}" opacity="0.85"/></g>')
    s.grain(0.05)


# theme: (generator, count, palettes, keywords used to match articles)
THEMES = {
    "lungs":     (t_lungs, 18, DARK + LIGHT, ["lung health", "respiratory health", "breath", "breathing", "lungs", "world lung", "respiratory disease"]),
    "xray":      (t_xray, 12, ["teal", "ink", "navy"], ["x-ray", "xray", "radiograph", "chest film", "pneumonia", "tuberculosis", " tb ", "imaging"]),
    "ctscan":    (t_ctscan, 12, ["teal", "navy", "ink", "sky"], ["screening", " ct ", "ct scan", "computed tomography", "nodule", "ldct", "radiolog", "lung cancer", "nsclc", "sclc", "tumour", "tumor", "oncolog", "mesothelioma"]),
    "pollution": (t_pollution, 16, ["sand"], ["air pollution", "pollution", "smog", "pm2.5", "pm 2.5", "particulate", "air quality", "aqi", "wildfire", "emission", "dust", "haze", "no2", "ozone"]),
    "city":      (t_city, 12, ["sky"], ["clean air", "climate", "cop31", "cop 31", "urban", "city", "cities", "active travel", "low emission", "green space", "net zero"]),
    "nature":    (t_nature, 12, DARK + LIGHT, ["environment", "planet", "forest", "heat", "climate change", "nature", "biodivers"]),
    "airways":   (t_airways, 15, DARK + LIGHT, ["copd", "emphysema", "bronchiectasis", "bronchitis", "exacerbation", "airway", "gold", "alveol", "rehabilitation", "pulmonary rehab", "small airway", "breathless", "dyspnoea", "dyspnea"]),
    "asthma":    (t_asthma, 14, DARK + LIGHT, ["asthma", "inhaler", "reliever", "gina", "wheez", "allerg", "spacer", "peak flow", "ics", "saba", "maintenance and reliever"]),
    "pharma":    (t_pharma, 16, DARK + LIGHT + ["coral"], ["drug", "fda", "ema", "approv", "biologic", "biosimilar", "phase 2", "phase 3", "phase ii", "phase iii", "inhibitor", "antibod", "mab ", "therapy", "treatment", "dose", "pharma", "medicine", "vaccine", "prescri", "label"]),
    "research":  (t_research, 14, DARK + LIGHT, ["study", "trial", "cohort", "meta-analysis", "systematic review", "randomi", "gene", "genetic", "biomarker", "researchers", "journal", "paper"]),
    "cells":     (t_cells, 12, ["teal"], ["cell", "neutrophil", "eosinophil", "immune", "inflammat", "fibrosis", "fibrotic", "ild", "interstitial", "granulom", "silica", "infection", "bacteria", "virus", "viral", "rsv", "influenza", "covid", "microbio", "pathway", "molecular"]),
    "tobacco":   (t_tobacco, 12, DARK + LIGHT, ["tobacco", "smok", "cigarette", "vape", "vaping", "e-cig", "nicotine", "varenicline", "cessation", "quit"]),
    "data":      (t_data, 14, DARK + LIGHT, ["spirometr", "lung function", "fev1", "data", "survey", "statistic", "figures", "prevalence", "burden", "mortality", "rate", "score", "predict", "model", "ai ", "artificial intelligence", "algorithm"]),
    "globe":     (t_globe, 14, DARK + LIGHT, ["who", "world health", "global", "policy", "government", "minister", "ministry", "law", "regulation", "un ", "united nations", "funding", "gdp", "cost", "report", "parliament", "nhs", "guideline", "strategy"]),
    "events":    (t_events, 12, DARK + LIGHT, ["congress", "conference", "symposium", "webinar", "summit", "session", "meeting", "livestream", "school", "course", "award", "appointed", "president", "elected", "podcast", "episode"]),
}


def palette_for(theme, i, choices):
    rnd = random.Random(f"pal-{theme}-{i}")
    return PAL[choices[i % len(choices)] if len(choices) > 1 and i < len(choices) else rnd.choice(choices)]


def render_one(theme, i, tmp: Path):
    gen, _, choices, _ = THEMES[theme]
    pal = palette_for(theme, i, choices)
    s = Svg(pal, seed=hash_seed(theme, i))
    gen(s, i)
    svg_path = tmp / f"{theme}-{i:02d}.svg"
    png_path = tmp / f"{theme}-{i:02d}.png"
    svg_path.write_text(s.svg(), encoding="utf-8")
    subprocess.run(["rsvg-convert", "-w", str(W), "-h", str(H), "-o", str(png_path), str(svg_path)], check=True)
    out = OUT / theme / f"{theme}-{i + 1:02d}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.open(png_path).convert("RGB").save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return out


def hash_seed(theme, i):
    import hashlib
    return int(hashlib.sha1(f"{theme}:{i}".encode()).hexdigest()[:8], 16)


def contact_sheet(theme, files, dest: Path):
    from PIL import Image, ImageDraw
    tw, th = 300, 158
    cols = 4
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 8) + 8, rows * (th + 26) + 8), "white")
    d = ImageDraw.Draw(sheet)
    for k, fp in enumerate(files):
        im = Image.open(fp).resize((tw, th))
        x, y = 8 + (k % cols) * (tw + 8), 8 + (k // cols) * (th + 26)
        sheet.paste(im, (x, y))
        d.text((x, y + th + 4), fp.name, fill="black")
    sheet.save(dest / f"{theme}.png")


def main(argv):
    sheet = "--sheet" in argv
    names = [a for a in argv if not a.startswith("--")] or list(THEMES)
    sheets = Path("/tmp/library-sheets")
    sheets.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        for theme in names:
            _, count, _, _ = THEMES[theme]
            for old in (OUT / theme).glob("*.jpg") if (OUT / theme).exists() else []:
                old.unlink()
            files = [render_one(theme, i, Path(td)) for i in range(count)]
            print(f"{theme}: {len(files)}")
            if sheet:
                contact_sheet(theme, files, sheets)
    index = {t: {"keywords": kw, "files": sorted(p.name for p in (OUT / t).glob("*.jpg"))}
             for t, (_, _, _, kw) in THEMES.items() if (OUT / t).exists()}
    (OUT / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    print("total:", sum(len(v["files"]) for v in index.values()))


if __name__ == "__main__":
    main(sys.argv[1:])

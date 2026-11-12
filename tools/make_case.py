#!/usr/bin/env python3
"""
Generate a 3D-printable two-part case (base + lid) for every board size.

    python3 tools/make_case.py            # writes case/stl/*.stl and case/images/*.png

The case is built from axis-aligned boxes on an exact rectilinear grid, so the
STL files are watertight and dimensionally exact (no curve approximation).

Stack-up (Z = 0 at the underside of the PCB):
  * base: floor, battery space under the board, ribs that press on the holder's
    end walls so the board assembly can't drop, walls up to just above the board,
    and a thin inner lip the lid slides over (with small snap bumps);
  * lid: walls and roof, small fingers that stop just above the board's long
    edges so it can't lift, a USB-C window for the XIAO, vent slots next to the
    SHTC3 sensor tab and across the roof.

Every dimension that depends on parts you buy is a parameter near the top of
this file (socket height, XIAO header height, cell diameter, clearances).
"""
import math, os, re, struct, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import make_gerbers as G  # noqa: E402

# ---------------------------------------------------------------- parameters
WALL = 2.4          # side wall thickness
FLOOR = 1.6         # base floor
ROOF = 1.6          # lid roof
CLR = 0.4           # gap between board/holder and the inner wall
CLR_USB = 1.4       # extra gap on the USB side: the XIAO's USB-C sticks out past the 23 mm boards
PCB_T = 1.6
SOCKET_H = 8.5      # female header height above the board (HX PM2.54 / common 8.5 mm)
HEADER_SPACER = 2.5 # plastic spacer of the XIAO's male headers
XIAO_PCB = 1.0
XIAO_PARTS = 3.5    # tallest part on top of the XIAO (USB-C shell 3.2 mm)
HEADROOM = 2.5      # free space above the XIAO (plug-in sensor boards, wires)
LIP_T = 1.0         # base lip thickness
LIP_H = 5.0         # base lip height above the split
LIP_GAP = 0.2       # lid-to-lip gap
SPLIT_ABOVE_PCB = 1.0   # base walls end this far above the PCB top
FINGER = 0.8        # how far the lid fingers reach over the board edge
FINGER_GAP = 0.3    # fingers stop this far above the board
BUMP = 0.35         # snap bump height
RIB_GAP = 0.3       # ribs stop this far below the holder
CELL_FLOOR = 1.0    # holder floor under the cell (cell sits this far from the board)
MOUNT_TABS = True   # screw tabs on the base ends for wall mounting

# Holders: Keystone part, body height above the board, cell diameter (max), cell name
HOLDERS = {
    '1096': dict(h=14.86, cell_d=18.5, cell='18350'),
    '1042': dict(h=14.86, cell_d=18.7, cell='18650'),
    '1121': dict(h=17.27, cell_d=21.4, cell='21700'),
}
# Holder body outline on the board (KiCad coordinates, from the B.Fab layer of each holder footprint)
HOLDER_BOX = {
    '1096': (124.42, 145.57, 56.20, 110.80),
    '1042': (124.56, 145.83, 25.75, 112.50),
    '1121': (124.47, 147.53, 27.45, 109.64),
}
CASES = [  # (name, board file, holder)
    ('18350', 'smart_sensor_battery.kicad_pcb', '1096'),
    ('18650', 'variants/18650/smart_sensor_battery.kicad_pcb', '1042'),
    ('21700', 'variants/21700/smart_sensor_battery.kicad_pcb', '1121'),
    ('universal-18350', 'variants/universal/smart_sensor_battery.kicad_pcb', '1096'),
    ('universal-18650', 'variants/universal/smart_sensor_battery.kicad_pcb', '1042'),
    ('universal-21700', 'variants/universal/smart_sensor_battery.kicad_pcb', '1121'),
]

# Front-side features (KiCad coordinates; identical on every board)
XIAO = dict(x0=125.46, x1=146.46, y0=81.73 - 8.9, y1=81.73 + 8.9)   # 21 x 17.8 mm module over J1/J2
USB_Y = 81.73
SENSOR_TAB = (124.0, 128.0, 106.75, 111.5)                          # SHTC3 tab
SOCKETS = [(126.9, 145.0, 58.85), (126.9, 145.0, 74.11), (126.9, 145.0, 89.35), (129.9, 143.1, 96.0)]


# ---------------------------------------------------------------- CSG on a rectilinear grid
class Solid:
    """A solid made of + / - axis-aligned boxes, evaluated exactly on the grid of all box faces."""
    def __init__(self):
        self.ops = []

    def add(self, x0, x1, y0, y1, z0, z1):
        self.ops.append((True, (min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1), min(z0, z1), max(z0, z1))))

    def sub(self, x0, x1, y0, y1, z0, z1):
        self.ops.append((False, (min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1), min(z0, z1), max(z0, z1))))

    def voxelize(self):
        axes = [sorted({round(b[i], 4) for _, b in self.ops for i in (2 * a, 2 * a + 1)}) for a in range(3)]
        self.axes = [np.array(a) for a in axes]
        sol = np.zeros([len(a) - 1 for a in axes], dtype=bool)
        for add, b in self.ops:
            sl = tuple(slice(np.searchsorted(self.axes[a], round(b[2 * a], 4)),
                             np.searchsorted(self.axes[a], round(b[2 * a + 1], 4))) for a in range(3))
            sol[sl] = add
        self.sol = sol
        return sol

    def occupied(self, x0, x1, y0, y1, z0, z1):
        """True if any solid cell overlaps the open box."""
        A = self.axes
        sl = []
        for a, (lo, hi) in enumerate(((x0, x1), (y0, y1), (z0, z1))):
            i0 = max(np.searchsorted(A[a], lo, side='right') - 1, 0)
            i1 = np.searchsorted(A[a], hi, side='left')
            sl.append(slice(i0, i1))
        return bool(self.sol[tuple(sl)].any())

    # -- meshing: greedy rectangles per plane, edges split at every grid line (watertight)
    def mesh(self):
        sol = self.sol; A = self.axes
        tris = []
        pad = np.pad(sol, 1)
        for ax in range(3):
            a1, a2 = [k for k in range(3) if k != ax]
            d = np.diff(pad.astype(np.int8), axis=ax)        # +1: empty->solid, -1: solid->empty
            d = d[tuple(slice(1, -1) if k != ax else slice(None) for k in range(3))]
            for sign in (1, -1):
                faces = (d == sign)
                for i in range(faces.shape[ax]):
                    m = np.take(faces, i, axis=ax).copy()
                    if not m.any():
                        continue
                    c = A[ax][i]
                    for (u0, u1, v0, v1) in greedy(m):
                        us = A[a1][u0:u1 + 1]; vs = A[a2][v0:v1 + 1]
                        loop = ([(u, vs[0]) for u in us[:-1]] + [(us[-1], v) for v in vs[:-1]] +
                                [(u, vs[-1]) for u in us[:0:-1]] + [(us[0], v) for v in vs[:0:-1]])
                        cu = (us[0] + us[-1]) / 2; cv = (vs[0] + vs[-1]) / 2
                        def P(u, v):
                            p = [0, 0, 0]; p[ax] = c; p[a1] = u; p[a2] = v; return tuple(p)
                        ctr = P(cu, cv)
                        # outward normal along +ax when sign == -1 (solid -> empty going up the axis)
                        out_pos = (sign == -1)
                        # loop runs counter-clockwise in (a1, a2); that is +ax for an even axis permutation
                        ccw_is_pos = ((a1, a2) in ((0, 1), (1, 2), (2, 0)))
                        flip = (out_pos != ccw_is_pos)
                        for k in range(len(loop)):
                            p1 = P(*loop[k]); p2 = P(*loop[(k + 1) % len(loop)])
                            tris.append((ctr, p2, p1) if flip else (ctr, p1, p2))
        return tris


def greedy(m):
    """Split a 2D boolean mask into rectangles (u0, u1, v0, v1), half-open in cell units."""
    m = m.copy(); out = []
    nu, nv = m.shape
    for u in range(nu):
        v = 0
        while v < nv:
            if not m[u, v]:
                v += 1; continue
            v1 = v
            while v1 < nv and m[u, v1]:
                v1 += 1
            u1 = u + 1
            while u1 < nu and m[u1, v:v1].all():
                u1 += 1
            m[u:u1, v:v1] = False
            out.append((u, u1, v, v1))
            v = v1
    return out


def write_stl(path, tris, name):
    with open(path, 'wb') as fh:
        fh.write(name.encode()[:80].ljust(80, b' '))
        fh.write(struct.pack('<I', len(tris)))
        for a, b, c in tris:
            n = np.cross(np.subtract(b, a), np.subtract(c, a)); L = np.linalg.norm(n)
            n = n / L if L else n
            fh.write(struct.pack('<12fH', *n, *a, *b, *c, 0))


def watertight(tris):
    from collections import Counter
    e = Counter()
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            e[(a, b)] += 1
    bad = sum(1 for (a, b), n in e.items() if e.get((b, a), 0) != n)
    return bad == 0, bad


# ---------------------------------------------------------------- geometry
def courtyards(path):
    """Front-side footprint courtyards (KiCad coordinates)."""
    s = open(path).read(); out = {}
    for m in re.finditer(r'\n\t\(footprint "[^"]+"', s):
        st = m.start() + 1; en = s.find('\n\t(footprint', st + 10)
        blk = s[st:en if en > 0 else len(s)]
        if '(layer "F.Cu")' not in blk[:300]:
            continue
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk).group(1)
        fx, fy, *fa = [float(v) for v in re.search(r'\n\t\t\(at ([^)]*)\)', blk).group(1).split()]
        fa = fa[0] if fa else 0
        pts = []
        for g in re.finditer(r'\(fp_(?:line|rect|poly|circle)(.*?)\(layer "F.CrtYd"\)', blk, re.S):
            for x, y in re.findall(r'\((?:start|end|xy|center) ([-\d.]+) ([-\d.]+)\)', g.group(1)):
                pts.append(G.xf(fx, fy, fa, float(x), float(y)))
        if pts:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            out[ref] = (min(xs), max(xs), min(ys), max(ys))
    return out


def board_box(path):
    ol = G.Board(path).outline()
    xs = [p[0] for p in ol]; ys = [p[1] for p in ol]
    return min(xs), max(xs), min(ys), max(ys)


def build(name, board_path, holder):
    hb = HOLDER_BOX[holder]; H = HOLDERS[holder]
    bx0, bx1, by0, by1 = board_box(os.path.join(ROOT, board_path))
    # inner cavity footprint (KiCad x, y). The universal board's cases all share one lid,
    # so their footprint covers all three holders.
    boxes = list(HOLDER_BOX.values()) if name.startswith('universal') else [hb]
    ix0 = min([bx0] + [b[0] for b in boxes]) - CLR; ix1 = max([bx1] + [b[1] for b in boxes]) + CLR_USB
    iy0 = min([by0] + [b[2] for b in boxes]) - CLR; iy1 = max([by1] + [b[3] for b in boxes]) + CLR
    z_cell = -(CELL_FLOOR + H['cell_d'])                 # far side of the cell
    z_floor = min(z_cell, -H['h']) - 1.0                 # floor top
    z_split = PCB_T + SPLIT_ABOVE_PCB
    z_xiao_top = PCB_T + SOCKET_H + HEADER_SPACER + XIAO_PCB + XIAO_PARTS
    z_roof = z_xiao_top + HEADROOM                        # roof underside
    ox0, ox1, oy0, oy1 = ix0 - WALL, ix1 + WALL, iy0 - WALL, iy1 + WALL

    base = Solid()
    base.add(ox0, ox1, oy0, oy1, z_floor - FLOOR, z_split)
    base.sub(ix0, ix1, iy0, iy1, z_floor, z_split + 1)
    # lip: inner part of the wall continues up
    base.add(ix0 - LIP_T, ix1 + LIP_T, iy0 - LIP_T, iy1 + LIP_T, z_split, z_split + LIP_H)
    base.sub(ix0, ix1, iy0, iy1, z_split - 1, z_split + LIP_H + 1)
    # snap bumps on the outside of the lip (long sides)
    bump_z = (z_split + LIP_H - 2.0, z_split + LIP_H - 1.2)
    L = iy1 - iy0
    bumps = [iy0 + L * f for f in (0.25, 0.75)]
    for yc in bumps:
        base.add(ix0 - LIP_T - BUMP, ix0 - LIP_T, yc - 3, yc + 3, *bump_z)
        base.add(ix1 + LIP_T, ix1 + LIP_T + BUMP, yc - 3, yc + 3, *bump_z)
    # ribs under the holder end walls
    rib_top = -H['h'] - RIB_GAP
    for ya, yb in ((hb[2] + 0.4, hb[2] + 2.4), (hb[3] - 2.4, hb[3] - 0.4)):
        base.add(hb[0] + 1.0, hb[1] - 1.0, ya, yb, z_floor - 0.01, rib_top)
    # vents in the floor under the sensor tab
    for k in range(3):
        x = 125.0 + k * 1.6
        base.sub(x, x + 0.8, 106.5, 111.0, z_floor - FLOOR - 1, z_floor + 0.5)
    # wall-mount tabs on both ends
    if MOUNT_TABS:
        cx = (ox0 + ox1) / 2
        for y_out, sgn in ((oy0, -1), (oy1, 1)):
            y_far = y_out + sgn * 9
            base.add(cx - 6, cx + 6, y_out, y_far, z_floor - FLOOR, z_floor - FLOOR + 2.4)
            yh = y_out + sgn * 5
            base.sub(cx - 1.7, cx + 1.7, yh - 1.7, yh + 1.7, z_floor - FLOOR - 1, z_floor + 2)

    lid = Solid()
    lz0 = z_split
    lid.add(ox0, ox1, oy0, oy1, lz0, z_roof + ROOF)
    lid.sub(ix0, ix1, iy0, iy1, lz0 - 1, z_roof)
    # room for the lip (+ gap)
    g = LIP_T + LIP_GAP
    lid.sub(ix0 - g, ix1 + g, iy0 - g, iy1 + g, lz0 - 1, z_split + LIP_H + 0.3)
    # recesses for the snap bumps
    for yc in bumps:
        lid.sub(ix0 - g - BUMP - 0.15, ix0, yc - 3.3, yc + 3.3, bump_z[0] - 0.15, bump_z[1] + 0.15)
        lid.sub(ix1, ix1 + g + BUMP + 0.15, yc - 3.3, yc + 3.3, bump_z[0] - 0.15, bump_z[1] + 0.15)
    # fingers over the long board edges: just above the board, inside the lip
    fz0 = PCB_T + FINGER_GAP
    fingers = []
    # two near each end of the board, moved to the nearest spot clear of part courtyards,
    # the XIAO (and its USB-C) and the sensor tab
    cy = list(courtyards(os.path.join(ROOT, board_path)).values())
    avoid_y = [(XIAO['y0'] - 1.0, XIAO['y1'] + 1.0)]
    def clear(x0, x1, y0, y1, left):
        if y0 < by0 + 0.5 or y1 > by1 - 0.5:
            return False
        if any(y1 > a0 and y0 < a1 for a0, a1 in avoid_y):
            return False
        if left and y1 > SENSOR_TAB[2] - 3.0:
            return False
        return not any(x1 + 0.2 > c[0] and x0 - 0.2 < c[1] and y1 + 0.2 > c[2] and y0 - 0.2 < c[3] for c in cy)
    for want in (by0 + 4.0, XIAO['y0'] - 4.0, XIAO['y1'] + 4.0, by1 - 7.0):
        for x0, x1, left in ((ix0, bx0 + FINGER, True), (bx1 - FINGER, ix1, False)):
            for off in sorted(np.arange(-8, 8.01, 0.5), key=abs):
                yc = want + off
                if clear(x0, x1, yc - 2, yc + 2, left):
                    fingers.append((x0, x1, yc - 2, yc + 2)); break
    for fx0, fx1, fy0, fy1 in fingers:
        lid.add(fx0, fx1, fy0, fy1, fz0, z_roof + 0.01)
    # USB-C window (right wall)
    z_usb = PCB_T + SOCKET_H + HEADER_SPACER + XIAO_PCB + 1.6
    lid.sub(ix1 - 1, ox1 + 1, USB_Y - 6.5, USB_Y + 6.5, z_usb - 4.5, z_usb + 4.5)
    # sensor-tab vents: left wall and bottom end wall, low down
    for k in range(4):
        y = 104.0 + k * 2.0
        lid.sub(ox0 - 1, ix0 + 0.1, y, y + 1.0, z_split + LIP_H + 0.8, z_split + LIP_H + 6.8)
    for k in range(3):
        x = 124.0 + k * 2.0
        lid.sub(x, x + 1.0, iy1 - 0.1, oy1 + 1, z_split + LIP_H + 0.8, z_split + LIP_H + 6.8)
    # roof vents (slots across the board), stopping short of the edges
    y = iy0 + 6
    while y < iy1 - 6:
        lid.sub(ix0 + 4, ix1 - 4, y, y + 1.2, z_roof - 1, z_roof + ROOF + 1)
        y += 4.0

    info = dict(name=name, holder=holder, inner=(ix0, ix1, iy0, iy1), outer=(ox0, ox1, oy0, oy1),
                z=(z_floor - FLOOR, z_split, z_roof + ROOF), z_floor=z_floor, z_cell=z_cell,
                z_xiao_top=z_xiao_top, z_usb=z_usb, board=(bx0, bx1, by0, by1), fingers=fingers,
                bumps=bumps, rib_top=rib_top, board_path=os.path.join(ROOT, board_path))
    return base, lid, info


def check(base, lid, info):
    """Interference checks against the parts the case has to hold."""
    H = HOLDERS[info['holder']]; hb = HOLDER_BOX[info['holder']]
    bx0, bx1, by0, by1 = info['board']
    e = 0.05
    keep = {
        'board': (bx0 - e, bx1 + e, by0 - e, by1 + e, 0, PCB_T),
        'holder body': (hb[0], hb[1], hb[2], hb[3], -H['h'] + e, 0),
        'cell': (hb[0] + 0.5, hb[1] - 0.5, hb[2] + 3, hb[3] - 3, info['z_cell'] + e, -CELL_FLOOR),
        'XIAO + USB-C': (XIAO['x0'], XIAO['x1'] + 1.3, XIAO['y0'], XIAO['y1'], PCB_T + SOCKET_H, info['z_xiao_top']),
        'USB-C plug path': (XIAO['x1'] + 1.3, info['outer'][1] + 5, USB_Y - 6.0, USB_Y + 6.0,
                            info['z_usb'] - 3.5, info['z_usb'] + 3.5),
        'sensor tab air': (SENSOR_TAB[0], SENSOR_TAB[1], SENSOR_TAB[2], SENSOR_TAB[3], PCB_T, PCB_T + 4),
    }
    for ref, (x0, x1, y0, y1) in courtyards(info['board_path']).items():
        keep['%s courtyard' % ref] = (x0, x1, y0, y1, PCB_T, PCB_T + 1.5)
    for (x0, x1, y) in SOCKETS:
        keep['socket y=%.2f' % y] = (x0, x1, y - 1.3, y + 1.3, PCB_T, PCB_T + SOCKET_H)
    # space for plug-in sensor boards above the sockets inside the lid
    keep['plug-in board space'] = (bx0 + FINGER + 0.1, bx1 - FINGER - 0.1, by0, by1,
                                   PCB_T + FINGER_GAP + 0.1, info['z_xiao_top'])
    res = []
    for k, b in keep.items():
        hits = [n for n, s in (('base', base), ('lid', lid)) if s.occupied(*b)]
        res.append((k, hits))
    return res


# ---------------------------------------------------------------- previews
def raster(tris, size=520, elev=28, azim=-55, color=(0.30, 0.45, 0.70)):
    """Tiny z-buffer renderer: orthographic view, flat shading. Returns an RGB array."""
    T = np.array(tris, dtype=float)
    a, e = math.radians(azim), math.radians(elev)
    Rz = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, math.cos(e - math.pi / 2), -math.sin(e - math.pi / 2)],
                   [0, math.sin(e - math.pi / 2), math.cos(e - math.pi / 2)]])
    V = T.reshape(-1, 3) @ Rz.T @ Rx.T
    mn, mx = V.min(0), V.max(0); sc = (size - 20) / max(mx[0] - mn[0], mx[1] - mn[1])
    P = np.empty_like(V); P[:, 0] = (V[:, 0] - mn[0]) * sc + 10; P[:, 1] = size - ((V[:, 1] - mn[1]) * sc + 10); P[:, 2] = V[:, 2]
    P = P.reshape(-1, 3, 3)
    N = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]); N /= np.linalg.norm(N, axis=1)[:, None] + 1e-12
    Nv = N @ Rz.T @ Rx.T
    light = np.array([-0.35, 0.45, 0.82]); light /= np.linalg.norm(light)
    shade = np.clip(Nv @ light, 0, 1) * 0.7 + 0.3
    img = np.ones((size, size, 3)); zb = np.full((size, size), -1e9)
    for t, (p0, p1, p2) in enumerate(P):
        if Nv[t, 2] <= 0:      # back-face culling
            continue
        x0, x1 = int(max(min(p0[0], p1[0], p2[0]), 0)), int(min(max(p0[0], p1[0], p2[0]) + 1, size))
        y0, y1 = int(max(min(p0[1], p1[1], p2[1]), 0)), int(min(max(p0[1], p1[1], p2[1]) + 1, size))
        if x1 <= x0 or y1 <= y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        d = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
        if abs(d) < 1e-12:
            continue
        w0 = ((p1[1] - p2[1]) * (xs - p2[0]) + (p2[0] - p1[0]) * (ys - p2[1])) / d
        w1 = ((p2[1] - p0[1]) * (xs - p2[0]) + (p0[0] - p2[0]) * (ys - p2[1])) / d
        w2 = 1 - w0 - w1
        m = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
        sub = zb[y0:y1, x0:x1]; m &= z > sub
        sub[m] = z[m]
        img[y0:y1, x0:x1][m] = np.array(color) * shade[t]
    return img


def section_png(path, base, lid, info, title):
    """Two cut-away views with the board, holder, cell and XIAO drawn in."""
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    H = HOLDERS[info['holder']]; hb = HOLDER_BOX[info['holder']]; bx0, bx1, by0, by1 = info['board']
    fig, axs = plt.subplots(1, 2, figsize=(14, 6.5), gridspec_kw=dict(width_ratios=[1, 2.6]))
    for ax, (cut_axis, cut_at, label) in zip(axs, ((1, USB_Y, 'cross-section through the XIAO / USB-C (y = %.1f)' % USB_Y),
                                                   (0, (bx0 + bx1) / 2, 'lengthwise section through the middle'))):
        for solid, col in ((base, '#4c72b0'), (lid, '#dd8452')):
            A = solid.axes; i = np.searchsorted(A[cut_axis], cut_at) - 1
            sl = np.take(solid.sol, i, axis=cut_axis)        # (other axis, z)
            other = 1 - cut_axis if cut_axis < 2 else 0
            U = A[other]; Z = A[2]
            for a in range(sl.shape[0]):
                for b in range(sl.shape[1]):
                    if sl[a, b]:
                        u0, u1 = U[a], U[a + 1]
                        if cut_axis == 0:   # lengthwise: show the board's top end on the left
                            u0, u1 = -u1, -u0
                        ax.add_patch(Rectangle((u0, Z[b]), u1 - u0, Z[b + 1] - Z[b], color=col, lw=0))
        def part(x0, x1, y0, y1, z0, z1, col, lab):
            u0, u1 = (x0, x1) if cut_axis == 1 else (-y1, -y0)
            ax.add_patch(Rectangle((u0, z0), u1 - u0, z1 - z0, fill=False, ec=col, lw=1.4, label=lab))
        part(bx0, bx1, by0, by1, 0, PCB_T, 'green', 'PCB')
        part(hb[0], hb[1], hb[2], hb[3], -H['h'], 0, 'black', '%s holder' % info['holder'])
        part(hb[0] + 1, hb[1] - 1, hb[2] + 3, hb[3] - 3, info['z_cell'], -CELL_FLOOR, 'red', '%s cell' % H['cell'])
        part(XIAO['x0'], XIAO['x1'] + 1.3, XIAO['y0'], XIAO['y1'], PCB_T + SOCKET_H + HEADER_SPACER, info['z_xiao_top'], 'purple', 'XIAO + USB-C')
        for (x0, x1, y) in SOCKETS:
            part(x0, x1, y - 1.3, y + 1.3, PCB_T, PCB_T + SOCKET_H, 'gray', None)
        ax.set_aspect('equal'); ax.autoscale(); ax.set_title(label, fontsize=11)
        ax.set_xlabel('mm'); ax.grid(alpha=0.2)
    axs[1].legend(loc='upper right', fontsize=9)
    fig.suptitle(title, fontsize=13); plt.tight_layout(); plt.savefig(path, dpi=80); plt.close(fig)


def render(path, parts, title):
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, len(parts), figsize=(5 * len(parts), 5.6))
    for ax, (label, tris, col, view) in zip(axs, parts):
        ax.imshow(raster(tris, color=matplotlib.colors.to_rgb(col), **view)); ax.axis('off'); ax.set_title(label, fontsize=12)
    fig.suptitle(title, fontsize=14); plt.tight_layout(); plt.savefig(path, dpi=80); plt.close(fig)


def to_case_frame(tris, info, flip=False):
    """KiCad (x right, y down) -> case frame (X right, Y toward the board's top end, Z up), origin at outer corner."""
    ox0, ox1, oy0, oy1 = info['outer']; zmin = info['z'][0]
    out = []
    for t in tris:
        q = []
        for (x, y, z) in t:
            X, Y, Z = x - ox0, oy1 - y, z - zmin
            if flip:  # print the lid roof-down: rotate 180 deg about the X axis
                Y, Z = (oy1 - oy0) - Y, info['z'][2] - zmin - Z
            q.append((X, Y, Z))
        # KiCad's y-down frame is mirrored relative to the real world, so swap two
        # vertices to keep the facets pointing outward (the lid flip is a proper rotation)
        out.append((q[0], q[2], q[1]))
    return out


def main():
    os.makedirs(os.path.join(ROOT, 'case', 'stl'), exist_ok=True)
    os.makedirs(os.path.join(ROOT, 'case', 'images'), exist_ok=True)
    report = []
    lids_done = {}
    for name, board, holder in CASES:
        base, lid, info = build(name, board, holder)
        base.voxelize(); lid.voxelize()
        problems = [(k, h) for k, h in check(base, lid, info) if h]
        bt = base.mesh(); lt = lid.mesh()
        okb, nb = watertight(bt); okl, nl = watertight(lt)
        ox0, ox1, oy0, oy1 = info['outer']
        size = (ox1 - ox0, oy1 - oy0, info['z'][2] - info['z'][0])
        bstl = to_case_frame(bt, info); lstl = to_case_frame(lt, info, flip=True)
        write_stl(os.path.join(ROOT, 'case', 'stl', 'case-%s-base.stl' % name), bstl, 'SSB case %s base' % name)
        lid_name = name.split('-')[0] if name.startswith('universal') else name
        if lid_name not in lids_done:
            write_stl(os.path.join(ROOT, 'case', 'stl', 'case-%s-lid.stl' % lid_name), lstl, 'SSB case %s lid' % lid_name)
            lids_done[lid_name] = True
        # preview: base, lid as printed, assembled
        asm_b = to_case_frame(bt, info); asm_l = to_case_frame(lt, info)
        render(os.path.join(ROOT, 'case', 'images', 'case-%s.png' % name),
               [('base', bstl, '#4c72b0', dict(elev=35, azim=-55)),
                ('lid (shown as printed, roof down)', lstl, '#dd8452', dict(elev=35, azim=-55)),
                ('lid, outside: USB-C window and vents', asm_l, '#dd8452', dict(elev=25, azim=35)),
                ('assembled', asm_b + asm_l, '#8c8c8c', dict(elev=22, azim=-60))],
               'Case for the %s board (%s holder): %.1f x %.1f x %.1f mm' % (name, holder, *size))
        section_png(os.path.join(ROOT, 'case', 'images', 'case-%s-section.png' % name), base, lid, info,
                    'Case for the %s board: sections (blue = base, orange = lid)' % name)
        vol = lambda T: sum(np.dot(a, np.cross(b, c)) for a, b, c in T) / 6.0
        info['volumes'] = (vol(bstl), vol(lstl))
        report.append((name, holder, size, len(bt), len(lt), okb and okl, problems, info))
        print('%-16s %-5s outer %.1f x %.1f x %.1f mm  tris base %d lid %d  watertight %s  vol %.1f/%.1f cm3  fingers %d  interference: %s'
              % (name, holder, *size, len(bt), len(lt), okb and okl, info['volumes'][0] / 1000, info['volumes'][1] / 1000,
                 len(info['fingers']), problems or 'none'))
    return report


if __name__ == '__main__':
    main()

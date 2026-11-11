#!/usr/bin/env python3
"""
Write JLCPCB assembly files (BOM + CPL/placement) for one board.

Part numbers come from the "LCSC Part no." field of each symbol in the project's
schematics; positions and rotations come from the .kicad_pcb, in the same coordinates
as the Gerbers written by make_gerbers.py (Y negated).

Parts without an LCSC number, or listed in --exclude, are left out of both files and
reported, so they can be soldered by hand (the battery holder on the back, for example).

Usage: python3 tools/make_jlc_bom.py <project_dir> <output_dir> --name <prefix> [--exclude BT1,U1]
"""
import csv, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_variant import parse, find, first, unq


# JLCPCB's library models for some parts use a different zero angle from KiCad.
# Footprint name -> degrees added to the KiCad rotation in the CPL file.
# Checked in JLCPCB's placement preview: the staggered pins land on the pads.
ROTATION_FIX = {
    'PinSocket_1x07_P2.54mm_Vertical_SMD_Pin1Right': 270,   # J2, J3 (C42379199)
    'PinSocket_1x07_P2.54mm_Vertical_SMD_Pin1Left': 90,     # J1 (C42379199)
    'PinSocket_1x05_P2.54mm_Vertical_SMD_Pin1Left': 270,    # J5 (C42379198)
    'SOT-23-5': 270,   # U4 (KiCad pads in columns, JLC part in rows)
    'SOT-23-6': 180,   # U2
    'SOT-23': 180,     # Q1, Q2
    'Sensirion_DFN-4-1EP_2x2mm_P1mm_EP0.7x1.6mm': 270,   # U7 SHTC3
}


def sch_fields(project_dir):
    parts = {}
    for fn in os.listdir(project_dir):
        if not fn.endswith('.kicad_sch'): continue
        t = parse(open(os.path.join(project_dir, fn)).read())
        for sy in find(t, 'symbol'):
            if not first(sy, 'lib_id'): continue
            P = {unq(p[1]): unq(p[2]) for p in find(sy, 'property')}
            ref = P.get('Reference', '')
            if ref.startswith('#'): continue
            parts[ref] = P
    return parts


def main():
    a = sys.argv[1:]
    proj, out = a[0], a[1]
    name = a[a.index('--name') + 1] if '--name' in a else 'board'
    exclude = set(a[a.index('--exclude') + 1].split(',')) if '--exclude' in a else set()
    fields = sch_fields(proj)
    b = parse(open(os.path.join(proj, 'smart_sensor_battery.kicad_pcb')).read())
    rows, skipped = [], []
    for fp in find(b, 'footprint'):
        P = {unq(p[1]): unq(p[2]) for p in find(fp, 'property')}
        ref = P['Reference']
        at = first(fp, 'at'); x, y = float(at[1]), float(at[2]); rot = float(at[3]) if len(at) > 3 else 0.0
        side = 'Bottom' if unq(first(fp, 'layer')[1]) == 'B.Cu' else 'Top'
        lcsc = fields.get(ref, {}).get('LCSC Part no.', '').strip()
        if ref in exclude or not lcsc:
            skipped.append((ref, P.get('Value', ''), 'excluded' if ref in exclude else 'no LCSC number'))
            continue
        fpname = unq(fp[1]).split(':')[-1]
        fix = ROTATION_FIX.get(fpname, 0)
        if '--rotfix' in a:
            fix = float(a[a.index('--rotfix') + 1]) if fix else 0
        rows.append(dict(ref=ref, value=P.get('Value', ''), fp=fpname, lcsc=lcsc,
                         x=x, y=-y, side=side, rot=(rot + fix) % 360))
    os.makedirs(out, exist_ok=True)
    # One BOM line per LCSC part: JLCPCB rejects several lines matched to the same part
    # (e.g. the three 1x7 sockets, whose names and pin-1 side differ).
    byp = {}
    for r in rows:
        byp.setdefault(r['lcsc'], []).append(r)
    groups = {}
    for lcsc, rs in byp.items():
        vals = sorted({r['value'] for r in rs}); fps = sorted({r['fp'] for r in rs})
        groups[(' / '.join(vals), fps[0] if len(fps) == 1 else fps[0].split('_P')[0] + '_P' + fps[0].split('_P', 1)[1].split('_Pin1')[0], lcsc)] = [r['ref'] for r in rs]
    key = lambda s: (''.join(c for c in s if c.isalpha()), int(''.join(c for c in s if c.isdigit()) or 0))
    with open(os.path.join(out, '%s-BOM.csv' % name), 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['Comment', 'Designator', 'Footprint', 'LCSC Part #'])
        for (val, fpn, lcsc), refs in sorted(groups.items(), key=lambda kv: key(sorted(kv[1], key=key)[0])):
            w.writerow([val, ','.join(sorted(refs, key=key)), fpn, lcsc])
    with open(os.path.join(out, '%s-CPL.csv' % name), 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        for r in sorted(rows, key=lambda r: key(r['ref'])):
            w.writerow([r['ref'], '%.4fmm' % r['x'], '%.4fmm' % r['y'], r['side'], '%g' % r['rot']])
    print('%s: %d parts in BOM (%d lines); not assembled: %s' % (name, len(rows), len(groups),
          ', '.join('%s %s (%s)' % s for s in skipped) or 'none'))


if __name__ == '__main__':
    main()

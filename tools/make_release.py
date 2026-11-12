#!/usr/bin/env python3
"""
Build one release zip per board option:  python3 tools/make_release.py 1.0
Writes release/SSB-<board>-v<version>.zip for 18350, 18650, 21700 and universal.

Each zip is self-contained: Gerbers for ordering, the JLCPCB BOM and CPL, the case STLs,
the ESPHome firmware and Home Assistant package, preview images, and a KiCad project
that opens on its own (footprint library, symbol library and 3D models next to it).
"""
import os, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = sys.argv[1] if len(sys.argv) > 1 else '1.0'

BOARDS = {
    '18350': dict(proj='.', gerber='SSB-18350', asm='SSB-18350', holder='Keystone 1096 (18350)',
                  size='23 x 56 mm', case=['case-18350-base.stl', 'case-18350-lid.stl'],
                  images=['docs/images/board_18350.png', 'case/images/case-18350.png', 'case/images/case-18350-section.png']),
    '18650': dict(proj='variants/18650', gerber='SSB-18650', asm='SSB-18650', holder='Keystone 1042 (18650)',
                  size='23 x 86.25 mm', case=['case-18650-base.stl', 'case-18650-lid.stl'],
                  images=['docs/images/board_18650.png', 'case/images/case-18650.png', 'case/images/case-18650-section.png']),
    '21700': dict(proj='variants/21700', gerber='SSB-21700', asm='SSB-21700', holder='Keystone 1121 (20700/21700)',
                  size='24 x 84.55 mm', case=['case-21700-base.stl', 'case-21700-lid.stl'],
                  images=['docs/images/board_21700.png', 'case/images/case-21700.png', 'case/images/case-21700-section.png']),
    'universal': dict(proj='variants/universal', gerber='SSB-UNIVERSAL', asm='SSB-UNIVERSAL',
                      holder='Keystone 1096 (18350), 1042 (18650) or 1121 (21700) - fit one',
                      size='24 x 86.25 mm',
                      case=['case-universal-18350-base.stl', 'case-universal-18650-base.stl',
                            'case-universal-21700-base.stl', 'case-universal-lid.stl'],
                      images=['docs/images/board_universal.png', 'case/images/case-universal-18350.png',
                              'case/images/case-universal-18650.png', 'case/images/case-universal-21700.png',
                              'case/images/case-universal-21700-section.png']),
}
PROJECT_FILES = ['smart_sensor_battery.kicad_pro', 'smart_sensor_battery.kicad_pcb', 'smart_sensor_battery.kicad_sch',
                 'smart_sensor_battery.kicad_prl', 'BMS.kicad_sch']
COMMON = ['README.md', 'LICENSE', 'firmware/smart-sensor-xiao-esp32c6.yaml', 'firmware/secrets.yaml.example',
          'homeassistant/smart_sensor_package.yaml', 'case/README.md']

FP_TABLE = '(fp_lib_table\n  (version 7)\n  (lib (name "Library")(type "KiCad")(uri "${KIPRJMOD}/Library.pretty")(options "")(descr ""))\n)\n'
SYM_TABLE = ('(sym_lib_table\n  (version 7)\n  (lib (name "smart_sensor_battery")(type "KiCad")'
             '(uri "${KIPRJMOD}/smart_sensor_battery.kicad_sym")(options "")(descr ""))\n)\n')


def readme(name, b, top):
    cases = '\n'.join('  case/%s' % c for c in b['case'])
    uni = ''
    if name == 'universal':
        uni = ('\nUNIVERSAL BOARD: fit only one holder. Print the case base for the holder you fitted\n'
               '(case-universal-18350/18650/21700-base.stl); all three use case-universal-lid.stl.\n')
    return f"""Smart Sensor Battery Pack - {name} board - v{VERSION}
{'=' * (40 + len(name))}
Battery holder: {b['holder']}
Board size:     {b['size']}
{uni}
WHAT'S IN THIS ZIP
  gerbers/{b['gerber']}-gerbers.zip   upload this to JLCPCB (or any PCB maker)
  assembly/{b['asm']}-BOM.csv        JLCPCB assembly: bill of materials (LCSC part numbers)
  assembly/{b['asm']}-CPL.csv        JLCPCB assembly: placement / rotation file
{cases}
  case/README.md                       case print settings and adjustable dimensions
  firmware/                            ESPHome config for the XIAO ESP32-C6 (+ secrets template)
  homeassistant/                       Home Assistant package (stay-awake toggle for updates)
  kicad/                               KiCad 9 project for this board; open kicad/smart_sensor_battery.kicad_pro
  images/                              board and case previews
  README.md                            full project documentation

ORDERING AT JLCPCB (tested November 2026)
  1. Upload the Gerber zip. Keep 2 layers, 1.6 mm, 1 oz. Choose LeadFree HASL
     (required for the cheaper Economic assembly).
  2. Turn on PCB Assembly: Economic, top side, quantity 2-5.
  3. Upload the BOM and CPL. The battery holder (BT1) is not in the BOM: buy it
     separately and solder it to the back by hand.
  4. Check the placement preview, then save to cart. See README.md, section
     "Tested JLCPCB order settings", for prices and tips.

NOTES
  - CPL rotations were checked part by part in JLCPCB's placement preview,
    including U6 (CSD85301Q2) at 90 degrees.
  - The case has not been test-printed yet: if your sockets or headers are a
    different height, see case/README.md.
  - Source, issues and updates: https://github.com/allenrobberson/smart-sensor-battery-pack/tree/battery-size-variants
  - Based on FeatureNAB/smart-sensor-battery-pack - thank you to the original author.
"""


def main():
    out_dir = os.path.join(ROOT, 'release'); os.makedirs(out_dir, exist_ok=True)
    for name, b in BOARDS.items():
        top = 'SSB-%s-v%s' % (name, VERSION)
        path = os.path.join(out_dir, top + '.zip')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            def add(src, arc):
                z.write(os.path.join(ROOT, src), '%s/%s' % (top, arc))
            z.writestr('%s/README-FIRST.txt' % top, readme(name, b, top))
            add('gerbers/%s-gerbers.zip' % b['gerber'], 'gerbers/%s-gerbers.zip' % b['gerber'])
            for k in ('BOM', 'CPL'):
                add('assembly/%s-%s.csv' % (b['asm'], k), 'assembly/%s-%s.csv' % (b['asm'], k))
            for c in b['case']:
                add('case/stl/' + c, 'case/' + c)
            for c in COMMON:
                add(c, c)
            for im in b['images']:
                add(im, 'images/' + os.path.basename(im))
            # self-contained KiCad project
            for f in PROJECT_FILES:
                add(os.path.join(b['proj'], f), 'kicad/' + f)
            add('smart_sensor_battery.kicad_sym', 'kicad/smart_sensor_battery.kicad_sym')
            z.writestr('%s/kicad/fp-lib-table' % top, FP_TABLE)
            z.writestr('%s/kicad/sym-lib-table' % top, SYM_TABLE)
            for d in ('Library.pretty', '3d_models'):
                for f in sorted(os.listdir(os.path.join(ROOT, d))):
                    add(os.path.join(d, f), 'kicad/%s/%s' % (d, f))
        print('%-40s %6.1f MB' % (os.path.relpath(path, ROOT), os.path.getsize(path) / 1e6))


if __name__ == '__main__':
    main()

# Smart Sensor Battery Pack (18350 / 18650 / 21700)

A small battery board for a Seeed XIAO microcontroller and an SHTC3 temperature and humidity sensor, for building wireless sensors that run on a single lithium-ion cell. The XIAO plugs into sockets on the front, and the cell sits in a holder on the back.

This fork of [FeatureNAB/smart-sensor-battery-pack](https://github.com/FeatureNAB/smart-sensor-battery-pack) adds two larger battery sizes. The original 18350 design is kept unchanged, and the 18650 and 21700 boards use the same circuit and the same front-side layout.

The original design, with the build, design theory and assembly, is explained in this video:

[![Watch the video](https://img.youtube.com/vi/-wEKOhVZ0NM/0.jpg)](https://www.youtube.com/watch?v=-wEKOhVZ0NM)

## What the board does
- **Microcontroller:** made for a Seeed **XIAO ESP32-C6**. The board uses D2 (GPIO2) for battery voltage, D4/D5 (GPIO22/23) for I2C and D10 (GPIO18) to switch the sensor power.
- **Charging:** a BQ21040 charges the cell from the XIAO's USB 5 V, and an LED shows charge status. R1 sets the charge current: 450 mA on the 18350 board and about 590 mA on the 18650 and 21700 boards, so a full charge fits inside the charger's 10-hour safety timer.
- **Battery temperature:** a 10 kΩ NTC thermistor (R2, Murata NCP15XH103F03RC) on the charger's TS pin stops charging when the board is too cold or too hot for lithium-ion. It sits in the corner furthest from the charger so the charger's own heat doesn't trip it.
- **Reverse-battery protection:** a P-channel MOSFET (Q2, AO3401A) in series with the cell's + terminal, with its gate on the cell's − terminal. A correctly inserted cell turns it fully on in both directions, for charging and discharging. A reversed cell leaves it off, and its body diode blocks the current.
- **Cell protection:** a BQ29700 protector with a CSD85301Q2 dual MOSFET guards against over-charge, over-discharge and over-current.
- **Power:** an RT9080-33GJ5 regulator (600 mA, 2 µA quiescent current) supplies 3.3 V to the XIAO. That covers the ESP32-C6's Wi-Fi transmit peaks (up to 354 mA at full power) with headroom, and a 10 µF capacitor (C3) on 3.3 V absorbs the bursts.
- **Battery monitoring:** a 5.1 MΩ / 5.1 MΩ divider brings the battery voltage to a XIAO analog pin (BATT_SENSE) and draws about 0.4 µA.
- **Switched sensor power:** a P-channel MOSFET (Q1, AO3401A) lets the XIAO turn the sensor rail (3V_SW) off between readings. The I2C pull-ups (R9, R10) are on that rail too, so nothing on the sensor side draws current while the XIAO sleeps.
- **Sensor:** an SHTC3 sits on a slotted tab at the bottom edge, thermally isolated from the rest of the board, with an extra socket for a plug-in I2C sensor.

## The three designs
All three boards share the same schematic and the same front-side parts and routing. Only the holder on the back, the board outline and the two battery traces change. The extra length is added at the BAT+ end, so the sensor tab stays where it is.

![All three boards, front and back](docs/images/board_comparison.png)

| Cell | Holder (back side) | Holder mounting | Board size | KiCad project |
|------|--------------------|-----------------|------------|---------------|
| 18350 | Keystone 1096 | Surface mount | 23 x 56 mm | repository root (original design) |
| 18650 | Keystone 1042 | Surface mount | 23 x 86.25 mm | `variants/18650/` |
| 21700 | Keystone 1121 (fits 20700 and 21700) | Through-hole solder lugs | 24 x 84.55 mm | `variants/21700/` |

### 18350
The original, most compact design. The Keystone 1096 holder covers the whole back of the board.

![18350 board, front and back](docs/images/board_18350.png)

### 18650
The same board stretched to 86.25 mm for a Keystone 1042 holder. The holder's locating holes are placed to clear all existing copper. The BAT- pad overhangs the bottom edge by 0.25 mm, which DRC will flag; the original board overhangs its BAT+ pad by 0.95 mm in the same way.

![18650 board, front and back](docs/images/board_18650.png)

### 21700
Uses a Keystone 1121 holder. The holder is 23.06 mm wide, so the board is 1 mm wider than the original, with the extra width on the right so the sensor tab is unchanged. The holder's solder lugs go through the board and are soldered from the front.

The footprint was drawn from Keystone drawing 1121 rev A. The lug holes are drilled 1.7 mm instead of the drawing's 1.32 mm, because the drawing gives the lug pin itself as 1.57 mm. Check a lug against the hole before ordering.

![21700 board, front and back](docs/images/board_21700.png)

## Before ordering boards
For any variant, open its project in KiCad 9, press **B** to refill the copper zones, and run DRC. This revision (reverse-battery FET, NTC, new regulator) and the variant boards were edited by script and checked with a custom clearance and connectivity check, but have not been run through KiCad's DRC, ordered or built yet.

### Known limitations
- **Cell temperature is measured on the board, not on the cell.** The thermistor sits on the front, above the cell, so it reads board temperature. Charging can pause on a hot day once the board passes about 45 °C.
- **A completely flat cell (near 0 V) won't charge.** Q2 needs a couple of volts across the cell to turn on. Cells that low shouldn't be recovered anyway.
- **USB plugged in with a nearly empty cell.** When the XIAO runs from USB, its own regulator also drives 3.3 V. If the cell is below about 3.0 V at that moment, the RT9080's output sits slightly above its input, past its 0.3 V reverse rating. The original design had the same behaviour; it lasts only until the cell charges past 3 V.

## Firmware (ESPHome, XIAO ESP32-C6)
A complete ESPHome config is in [`firmware/smart-sensor-xiao-esp32c6.yaml`](firmware/smart-sensor-xiao-esp32c6.yaml). It does the following:

- **Deep sleep:** the node wakes, reads temperature, humidity and battery voltage, reports them, and sleeps again. By default it stays awake 20 s and sleeps 5 minutes; change `awake_time` and `sleep_time` at the top.
- **Sensor power sequencing:** GPIO18 is driven low at boot priority 1200, before I2C starts (priority 1000), so the sensor and its pull-ups are powered when the bus is set up.
- **No phantom powering:** the ESP32-C6's internal I2C pull-ups are turned off. Just before deep sleep, SDA, SCL and GPIO18 are set to high-impedance with no pulls; R7 then switches the sensor rail off. Nothing can drive the sensor through its data pins while it is unpowered.
- **Lower radio power:** Wi-Fi uses `fast_connect`, light power-save and 15 dB transmit power to shorten and cheapen each wake-up. The regulator handles full power if you need the range.
- **Antenna:** the XIAO's RF switch is enabled with its built-in antenna selected. Set GPIO14 high in the boot lambda to use the U.FL connector.

- **Stay-awake switch:** while `input_boolean.smart_sensor_stay_awake` is on in Home Assistant, the node skips deep sleep so you can update it over the air or watch its logs. It picks the change up the next time it wakes.

### Setting it up in Home Assistant
1. **Install ESPHome:** add the ESPHome Device Builder add-on in Home Assistant (Settings → Add-ons), or use the `esphome` command line on your computer.
2. **Add the secrets:** copy [`firmware/secrets.yaml.example`](firmware/secrets.yaml.example) to `secrets.yaml` and fill in your Wi-Fi details, an API encryption key, an OTA password and a fallback-hotspot password. In the Device Builder, open **Secrets** (top right) and paste the same keys there. `secrets.yaml` is ignored by git, so it won't be committed.
3. **Add the device:** in the Device Builder choose **New device**, skip the wizard, and paste the contents of `smart-sensor-xiao-esp32c6.yaml` into the new device's file. You can change `name`, `awake_time` and `sleep_time` at the top.
4. **First flash over USB:** plug the XIAO into your computer and choose **Install → Plug into this computer** (or run `esphome run smart-sensor-xiao-esp32c6.yaml`). Later updates can go over Wi-Fi.
5. **Add it to Home Assistant:** it shows up under Settings → Devices & Services as a discovered ESPHome device. Click **Configure** and enter the same API encryption key.
6. **Add the stay-awake toggle:** copy [`homeassistant/smart_sensor_package.yaml`](homeassistant/smart_sensor_package.yaml) to `<config>/packages/` and add this to `configuration.yaml`, then restart Home Assistant:
   ```yaml
   homeassistant:
     packages:
       smart_sensor: !include packages/smart_sensor_package.yaml
   ```
   To update the firmware later, turn the toggle on, wait for the sensor's next wake-up, install the update, then turn the toggle off.

The node appears with **Temperature**, **Humidity**, **Battery voltage** and **Battery level** entities. While it sleeps, Home Assistant shows them as unavailable between reports; that is normal for a deep-sleep device.

### Sleep current
While the XIAO sleeps, the board itself draws only a few microamps: the regulator about 2 µA, the protector about 4 µA, the charger's battery leakage about 1 µA and the divider 0.4 µA. The XIAO's own deep-sleep current comes on top; the ESP32-C6 chip alone is rated at 7 µA.

## Repository layout
```
smart_sensor_battery.kicad_*   18350 board (master design)
BMS.kicad_sch                  battery management sub-sheet
Library.pretty/                footprints for all three boards
3d_models/                     3D models
variants/18650/                18650 board (KiCad project)
variants/21700/                21700 board (KiCad project)
tools/make_variant.py          generates the variant boards from the master
firmware/                      ESPHome config and secrets template for the XIAO ESP32-C6
homeassistant/                 Home Assistant package (stay-awake toggle)
docs/images/                   board previews used in this README
```

## Changing the design
The root (18350) project is the master. Make circuit or layout changes there, then regenerate the larger boards so they pick up the change (Python 3, no extra packages):

```
python3 tools/make_variant.py --name 18650 \
    --footprint Library.pretty/BatteryHolder_Keystone_1042_1x18650.kicad_mod \
    --value 1042 --mpn "Keystone 1042" --neg-y 108.08 --set R1=910

python3 tools/make_variant.py --name 21700 \
    --footprint Library.pretty/BatteryHolder_Keystone_1121_1x21700.kicad_mod \
    --value 1121 --mpn "Keystone 1121" --cell 21700 --set R1=910
```

The script swaps the holder, resizes the outline, extends the battery traces, stretches the ground pours and updates BT1 in the schematic. `--set REF=VALUE` overrides a part value in that variant; the larger cells use it to raise the charge current. It also reports any holder hole that would land on existing copper; use `--neg-y` to slide the holder along the board if one does. To add another cell size, point `--footprint` at that holder's KiCad footprint.

The 18650 footprint comes from the official KiCad library (CC-BY-SA 4.0 with the KiCad library exception), with its pads renamed P/N to match the BT1 symbol.

## ⚠️ Safety Warning
This board involves **Lithium-Ion battery charging**. 
*   This is an **experimental prototype** from a hobbyist. **I am not a professional engineer**. It has **not** been certified by safety agencies (UL, CE, etc.).
*   Lithium batteries can be dangerous if mishandled (risk of thermal runaway, fire, explosion, and serious injury).
*   **DO NOT leave this device unattended with a battery connected**
*   By using these files, you agree that you are solely responsible for verifying the safety of the circuit.

## License & Liability

### 1. Non-Commercial Use Only
This project is shared under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/) license.
*  **You are free to:** Download, modify, and build this for personal use or learning.
*  **You may NOT:** Sell this board, use it in a product you sell, or use these files for any commercial purpose.
*  If you modify and share this design, you must share it under this same license.

### 2. No Liability (Disclaimer)
These files are provided "As Is" for educational purposes ONLY. I am sharing my personal project notes. **I accept no liability for any damage, injury, or loss** that occurs from the use or assembly of these files. Use them at your own risk.

## Credits
Original design by [FeatureNAB](https://github.com/FeatureNAB/smart-sensor-battery-pack). The 18650 and 21700 variants are shared under the same CC BY-NC-SA 4.0 license.

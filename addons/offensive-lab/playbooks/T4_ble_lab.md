# T4 — Lab BLE peripherals

## Goal
Exercise Mayhem BLE Rx/Tx/Spam **only** against lab-owned beacons/phones/dongles.

## PortaPack
1. BLE Rx — enumerate lab advertisers.  
2. BLE Tx — crafted adverts to lab scanner.  
3. BLESpam — load `BLESPAM/LAB_TEST_BEACON.txt` names; keep power low / distance short.  
4. Stop when lab DUT test is done; do not leave spam running unattended.

## Rule
If the MAC/name is not a known lab asset → skip.

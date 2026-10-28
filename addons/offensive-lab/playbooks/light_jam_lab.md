# Light / local jam — lab stress only

Per SCOPE `PP-LAB-RF-2026`:

- Short duration (script default max 5s unless overridden with hard cap 15s)  
- Narrow center (single LO)  
- AMP **off**  
- Operator attended  
- Inside closed lab  

```bash
./addons/offensive-lab/scripts/lab_light_jam.sh <freq_hz> <seconds>
# requires: PP_LAB_CONFIRM=PP-LAB-RF-2026
```

Hard/broadband/long jamming remains forbidden.

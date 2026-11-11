# Light / local jam — lab stress only

Per your local `SCOPE.md` (light / local jam must be explicitly allowed):

- Short duration (script default max 5s unless overridden with hard cap 15s)  
- Narrow center (single LO)  
- AMP **off**  
- Operator attended  
- Inside closed lab  

```bash
./addons/offensive-lab/scripts/lab_light_jam.sh <freq_hz> <seconds>
# requires: PP_LAB_CONFIRM=<your-engagement-id matching CONFIRM.md>
```

Hard/broadband/long jamming remains forbidden.

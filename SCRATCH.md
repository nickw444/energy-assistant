Scenarios

| Mode                           | Description                                                        | Inverter Mode    | Export Limit   | Force Charge Power | Force Discharge Power | EV Charger Power       |
| ------------------------------ | ------------------------------------------------------------------ | ---------------- | -------------- | ------------------ | --------------------- | ---------------------- |
| **SELF_CONSUME**               | Maximise local PV/battery usage; export PV when battery full       | `SelfUse`        | `Inverter max` | –                  | –                     | `0 kW`                 |
| **SELF_CONSUME_CURTAIL**       | Never export PV; curtail solar when battery is full                | `SelfUse`        | `0 kW`         | –                  | –                     | `0 kW`                 |
| **EXPORT_MAX**                 | Maximise export using PV + battery; no grid import                 | `ForceDischarge` | `Inverter max` | –                  | `Inverter max`        | `0 kW`                 |
| **GRID_CHARGE_BATTERY**        | Charge battery as fast as possible from PV + grid; house from grid | `ForceCharge`    | –              | `Inverter max`     | –                     | `0 kW`                 |
| **GRID_CHARGE_EV**             | Charge EV from grid while forcing inverter to absorb all PV        | `ForceCharge`    | –              | `0.1 kW`           | –                     | `Target EV kW`         |
| **EV_FROM_PV**                 | Solar-follow EV charging; no grid import for EV                    | `SelfUse`        | –              | –                  | –                     | `Dynamic ≤ PV surplus` |
| **EV_FROM_BATTERY**            | Charge EV using PV + battery (no forced discharge)                 | `SelfUse`        | –              | –                  | –                     | `Target EV kW`         |
| **GRID_CHARGE_EV_AND_BATTERY** | Grid supplies house, EV, and battery at max rate; PV curtailed     | `ForceCharge`    | –              | `Inverter max`     | –                     | `Target EV kW`         |

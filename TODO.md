## EMS

- [x] Improve output models to reflect plant heirarchy
- [x] Better/explicit data passing between builders, rather than shared mutations
- [x] Refactoring to improve readability (Less AI Slop)
- [x] Implement a class for the solver instead of using adhoc module level functions
- [x] Remove import forbidden periods from the horizon 
- [x] Verify that incentives don't show in total (real) cost values
- [x] model outputs should round to 3dp.
- [x] dynamically set horizon to shortest forecast length
- [x] Dynamic horizon timestep lengths
- [ ] Implement more modular way to handle objective function
- [ ] Implement slot -1 (pre-MPC slot) as model inputs
- [ ] Better curtailment behaviour: Encourage curtailment even when export is going to be 0, when price is negative.
- [ ] cache Home Assistant history values used for forecasts so we can generate a plan faster.
- [ ] Implement new load types: constant/nonvariable load.
- [ ] Reduce binaries and replace with continuous variables/quantisation
- [ ] Fix EV charging - charging never starts.
- [ ] Better handling of incomplete horizons for our SoC terminal constraints.

## Worker

- [x] Based on configuration, eagerly re-plan when realtime cost changes.
- [x] Observe price sensor and reevaluate on new price value

## Home Assistant Integration

- [ ] Review/Unslopify the Home Assistant integration
- [x] Main Device returning slot parameters
    - MPC values from Slot 0
- [ ] Sub-Devices for Loads
    - EV Loads
        - [ ] Dynamic Charge By Time + Charge By Reward
        - [ ] Connect Requested By (e.g. the time charge will start based on the plan)
- [ ] Use Home Assistant integration to feed entity states to the API rather than separate token access.
- [ ] Make the quantiser modulate charge rates


## Home Assistant Automations/Integrations

- [x] Control inverter curtailment
- [x] Control inverter work modes
- [ ] Notify when EV connect is requested
- [ ] Start/Stop/Modulate EV charging rate

## Quantization Layer (builtin)

- [ ] Quantize outputs into discrete inverter actions
- [ ] Support outputs directly to Home Assistant.
  - [ ] Pre-defined modules for inverter integrations

## API

- [x] Improve API models to reflect plant heirarchy 

## Web UI

- [ ] Implement a modern/sleek looking Web UI for setup/configuration, instead of relying on config file.
- [ ] Setup Wizard / Configuration
    - Connect Home Assistant
    - Plant Setup (Connect Hass Entities)
    - Loads Setup
- [ ] Main View: Realtime History, Plan & Outputs View


## Predictions

- [x] Wrap load forecast so we can extend the forecast dynamically to price forecast length
- [x] Linearly interpolate realtime load value into load forecast so that high realtime loads are factored into forecast.
- [ ] Predict longer term pricing forecasts based on historical data with an ML model
    - Note: Sometimes Amber and AEMO forecasts are truncated? So ML based forecast predictor will be necessary to produce a good plan. - Seems to be based on AEMO pricing publishing times.
    - If price forecast is truncated, then we could allow % based terminal SoC? e.g. 12hr forecast -> 50% of current SoC.?


## Tests


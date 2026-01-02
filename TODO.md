## EMS

- [x] Improve output models to reflect plant heirarchy
- [x] Better/explicit data passing between builders, rather than shared mutations
- [x] Refactoring to improve readability (Less AI Slop)
- [x] Implement a class for the solver instead of using adhoc module level functions
- [x] Remove import forbidden periods from the horizon 
- [x] Verify that incentives don't show in total (real) cost values
- [x] model outputs should round to 3dp.
- [ ] Implement more modular way to handle objective function
- [ ] Implement slot -1 (pre-MPC slot) as model inputs
- [ ] Better curtailment behaviour: Encourage curtailment even when export is going to be 0, when price is negative.
- [ ] cache Home Assistant history values used for forecasts so we can generate a plan faster.
- [ ] dynamically set horizon to shortest forecast length
- [ ] Implement new load types: constant/nonvariable load.

## Worker

- [ ] Based on configuration, eagerly re-plan when realtime cost changes.

## Home Assistant Integration

- [ ] Review/Unslopify the Home Assistant integration
- [ ] Main Device returning slot parameters
    - MPC values from Slot 0
- [ ] Sub-Devices for Loads
    - EV Loads
        - [ ] Dynamic Charge By Time + Charge By Reward
        - [ ] Connect Requested By (e.g. the time charge will start based on the plan)
- [ ] Graphing outputs (camera entity?)


## Home Assistant Automations/Integrations


- [ ] Control inverter curtailment
- [ ] Control inverter work modes
- [ ] Notify when EV connect is requested
- [ ] Start/Stop/Modulate EV charging rate

## API

- [ ] Improve API models to reflect plant heirarchy 

## Web UI

- [ ] Implement a modern/sleek looking Web UI for setup/configuration, instead of relying on config file.
- [ ] Setup Wizard / Configuration
    - Connect Home Assistant
    - Plant Setup (Connect Hass Entities)
    - Loads Setup
- [ ] Main View: Realtime History, Plan & Outputs View


## Predictions

- [ ] Predict longer term pricing forecasts based on historical data with an ML model
    - Note: Sometimes Amber and AEMO forecasts are truncated? So ML based forecast predictor will be necessary to produce a good plan.
    - If price forecast is truncated, then we could allow % based terminal SoC? e.g. 12hr forecast -> 50% of current SoC.?
- [ ] Linearly interpolate realtime load value into load forecast so that high realtime loads are factored into forecast.

## Tests


## EMS

- [ ] Improve output models to reflect plant heirarchy
- [ ] Better/explicit data passing between builders, rather than shared mutations
- [ ] Refactoring to improve readability (Less AI Slop)
- [ ] Implement slot -1 (pre-MPC slot) as model inputs
- [ ] Better curtailment behaviour: Encourage curtailment even when export is going to be 0, when price is negative.
- [x] Remove import forbidden periods from the horizon 
- [ ] Verify that incentives don't show in total (real) cost values
- [ ] Implement a class for the solver instead of using adhoc module level functions

## Home Assistant Integration

- [ ] Main Device returning slot parameters
    - MPC values from Slot 0
- [ ] Sub-Devices for Loads
    - EV Loads
        - [ ] Dynamic Charge By Time + Charge By Reward
        - [ ] Connect Requested By (e.g. the time charge will start based on the plan)
- [ ] Graphing outputs (camera entity?)

## Home Assistant Automations/Integrations

- [ ] Review/Unslopify the Home Assistant integration
- [ ] Control inverter curtailment
- [ ] Control inverter work modes
- [ ] Notify when EV connect is requested
- [ ] Start/Stop/Modulate EV charging rate

## API

- [ ] Improve API models to reflect plant heirarchy 

## Web UI

- [ ] Implement a modern/sleek looking Web UI for setup/configuration, instead of relying on config file.


## Predictions

- [ ] Predict longer term pricing forecasts based on historical data with an ML model
    - Note: Sometimes Amber and AEMO forecasts are truncated? So ML based forecast predictor will be necessary to produce a good plan.
    - If price forecast is truncated, then we could allow % based terminal SoC? e.g. 12hr forecast -> 50% of current SoC.?
- [ ] Linearly interpolate realtime load value into load forecast so that high realtime loads are factored into forecast.

## Tests


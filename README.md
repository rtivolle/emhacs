# Emporia Vehicle Vue - Home Assistant Integration

Creates a "battery" device sensor in Home Assistant to track vehicles configured in the Emporia system.  
Also creates status and power sensors for Emporia EV Chargers so you can see charger state, whether it's on, and its current/max charging rate from Home Assistant.

- Requires an Emporia account with a vehicle registered in the Emporia account.
- Entity name in  HA will be set to name of vehicle as configured in Emporia.
- Integration polls Emporia every 30 minutes for each vehicle to update the battery status and charging state.  This may hit rate limits if you have multiple vehicles (not tested with multiple vehicles - Emporia has monthly rate limits).
- Extra attributes contain attributes such as charge state. 
- Emporia EV chargers are exposed as status sensors with attributes for charger on/off, message, and charging rate, and a power sensor that reports current charging power. Power is pulled from Emporia’s usage API (1-second scale) when available, with an amps→kW fallback using an assumed 240V split-phase supply.

https://my.home-assistant.io/redirect/config_flow_start?domain=vehiclevue

This integration is not affiliated with or approved by Emporia - but makes their products a bit more useful!

<a href="https://my.home-assistant.io/redirect/config_flow_start?domain=vehiclevue" class="my badge" target="_blank"><img src="https://my.home-assistant.io/badges/config_flow_start.svg"></a>

------

### Example:

<img width="840" alt="image" src="https://github.com/user-attachments/assets/3c4161b3-5858-49a9-91e9-f3c2e1fc32cd" />

## Quick local check

To verify your Emporia credentials and see fetched vehicles/chargers before loading into HACS/Home Assistant:

```bash
EMPORIA_EMAIL="user@example.com" EMPORIA_PASSWORD="supersecret" python scripts/dev_check.py
```

Interactive charger check (prompts for credentials):

```bash
python scripts/verify_charger.py
```

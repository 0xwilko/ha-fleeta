# FLEETA (BlackVue) for Home Assistant

Brings a BlackVue cloud dashcam into Home Assistant through the official
[FLEETA Open API](https://developers.fleeta.io/).

For each dashcam on your FLEETA account you get:

| Entity | What it shows |
|---|---|
| Device tracker | The vehicle's GPS position on the map, and home/away from your zones |
| Speed | Last reported speed (shown in mph by default) |
| Status | Driving, parked or offline, as reported by FLEETA |
| Last GPS fix | When the position was last updated |
| Dashcam event | Fires on parking impacts, driving impacts, harsh braking and other camera events |
| Cloud connection | Whether the camera is connected to the FLEETA cloud |
| Battery | Battery pack level (only if a BlackVue battery pack is connected) |

An account device also shows **API calls this month** and the current **Update interval**.

## Requirements

- An active FLEETA cloud subscription that includes Open API access.
- A FLEETA API key: in the FLEETA web viewer go to **Management › Open API › API keys** and issue a key (it starts with `flt_live_`).

## Install

1. In HACS, open the menu (⋮) › **Custom repositories**.
2. Add `https://github.com/0xwilko/ha-fleeta` with type **Integration**.
3. Download **FLEETA (BlackVue)** and restart Home Assistant.
4. **Settings › Devices & services › Add integration › FLEETA (BlackVue)**, then paste the API key.

## How often it updates

FLEETA gives each account a monthly allowance of API calls. The integration reads
the allowance from FLEETA and paces itself so it is never used up before the month
ends: it checks more often while the car is moving and less often while it is
parked. On a Pro allowance with one camera that is roughly every 5 minutes while
driving and every 15 minutes while parked.

The option **Never check more often than every (minutes)** can slow it down further
if you use the API elsewhere (for example an AI assistant connected to FLEETA's MCP
server). It cannot make it poll faster than the allowance allows.

## Notes

- FLEETA's status field can lag. Check **Last GPS fix** to see how fresh the position is.
- Dashcam events arrive with the next update, so a parking impact can take up to one
  update interval to appear.
- The API key is stored only in Home Assistant. Diagnostics downloads remove the key,
  the camera serial number and GPS positions.
- Read-only: the integration never changes camera settings and never requests video,
  so it does not use your video allowance or the camera's mobile data.

This is an unofficial community integration. It is not made or endorsed by BlackVue (Pittasoft).

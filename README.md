# Gemstone Lights for Home Assistant

A custom integration for [Gemstone Lights](https://www.gemstonelights.com/)
permanent outdoor lighting, controlling the **Hub2** controller from Home
Assistant.

Gemstone does not publish an API (one is expected in 2027), and the Hub2 has no
usable local control. This integration talks to the same cloud API the official
mobile app uses.

## Features

- **Light entity** - on/off, RGB colour, and brightness.
- **Design select** - play any saved design, including designs that target
  individual zones, so you can drive front and back independently.
- **Pattern select** - play any pattern saved in your account's folders.
- **Now playing sensor** - what the controller is currently showing, plus
  firmware, output names and local IP as attributes.
- Devices are discovered automatically across every homegroup on the account.

## Installation

### HACS (recommended)

1. In HACS, choose **Integrations** → three-dot menu → **Custom repositories**.
2. Add this repository's URL with category **Integration**.
3. Install **Gemstone Lights**, then restart Home Assistant.

### Manual

Copy `custom_components/gemstone_lights` into your Home Assistant
`custom_components` directory and restart.

## Setup

**Settings → Devices & Services → Add Integration → Gemstone Lights**, then sign
in with the same email and password you use in the Gemstone Lights app.

Your credentials are stored in the config entry so the integration can renew its
session; Gemstone's tokens are short-lived and its refresh tokens expire after
roughly 30 days. If the password ever changes, Home Assistant will prompt you to
re-authenticate.

## Notes and limitations

- **Cloud only.** Every command goes through Gemstone's servers, so the
  integration needs internet access and inherits their latency. The Hub2's
  "Allow Local Commands" option only serves the Control4 driver, whose protocol
  is not published.
- **Brightness** is expressed by scaling the RGB value, which is the usual
  convention for RGB-only lights. Patterns and designs carry their own
  brightness, set when they were created.
- **State lags slightly.** The cloud takes a moment to reflect a change, so the
  integration re-reads state a couple of seconds after each command and polls
  every 30 seconds.
- **Music mode and playlists** are not implemented.

## The API, for anyone building on this

Everything below was determined by observing the official Android app. It is
recorded here so nobody has to repeat the work.

Auth is **AWS Cognito** (user pool `us-west-2_rr5lY7Etr`, client
`2647t144niotrl53vvru0ivno7`, SRP). Requests then carry a plain bearer token —
**there is no AWS request signing**, despite the error messages suggesting
otherwise.

Base URL: `https://mytpybpq12.execute-api.us-west-2.amazonaws.com/prod`

Required headers:

```
authorization: Bearer <cognito access token>
app-environment: Production
content-type: application/json
```

Two traps cost the most time:

1. **All writes are `PUT`.** Sending `POST` returns
   `Invalid key=value pair (missing equal-sign) in Authorization header`, which
   looks like a SigV4 signing failure and is thoroughly misleading.
2. **The query parameter name differs by endpoint.** Most take
   `deviceOrGroupId`; the architectural (per-zone) endpoints take `deviceId`.

| Method | Path | Params | Body |
| --- | --- | --- | --- |
| GET | `/homegroup/list` | | |
| GET | `/homegroup/devices` | `homegroupId` | |
| GET | `/deviceControl/currentlyPlaying` | `deviceOrGroupId` | |
| GET | `/deviceControl/zone/list` | `deviceId` | |
| GET | `/deviceControl/architectural/list` | `deviceId` | |
| GET | `/folders/list` | | |
| GET | `/folders/pattern/list` | `folderId` | |
| PUT | `/deviceControl/onState` | `deviceOrGroupId` | `{"onState": true}` |
| PUT | `/deviceControl/play/color` | `deviceOrGroupId` | `{"color": 65280}` |
| PUT | `/deviceControl/play/pattern` | `deviceOrGroupId` | `{"pattern": {...}}` |
| PUT | `/deviceControl/play/architectural` | `deviceId` | `{"architectural": {...}}` |

Colours are `0xRRGGBB` integers. `currentlyPlaying` returns `onState`, `color`,
`pattern`, `architectural` and `playlist`, where only one of the last three is
set at a time. Pattern bodies are the `patternData` object from
`/folders/pattern/list`; design bodies are an object from
`/deviceControl/architectural/list` with `preview: false` added.

## Disclaimer

Not affiliated with, endorsed by, or supported by Gemstone Lights. It relies on
a private API that the vendor may change at any time. Use at your own risk.

## License

MIT

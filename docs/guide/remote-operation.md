---
description: Use RigPlane away from the station safely — keep the web server off the internet, reach it over your own VPN or an SSH tunnel, and meet the browser's microphone requirement.
---

# Operating Away From Home

The RigPlane web server has no login. Anyone who can reach its port can
control the radio, including transmit: the listener itself is the boundary
(see [Application authentication removal](../api/web.md#application-authentication-removal)).
Keep that port off the internet and reach it only through a path you control.

## Keep the Server Off the Internet

- Do not forward the web port (8080 by default) on your router.
- `rigplane web` listens on all interfaces by default (`--host 0.0.0.0`). On a
  machine that other networks can reach, pass `--host 127.0.0.1` and come in
  through an SSH tunnel, or block the port with a firewall.

## Reach It Over an SSH Tunnel

From the remote computer, forward a local port to the station:

```bash
ssh -L 8080:localhost:8080 you@station
```

Then open `http://localhost:8080`. The browser treats `localhost` as a secure
origin, so voice TX from the browser microphone works without a certificate.

## Reach It Over Your Own VPN

With WireGuard, Tailscale or a similar VPN, open the station by its VPN IP
address. Everyone else on that VPN can reach the port too, and with it the
radio, including transmit, so use a VPN that only you (and people you trust
with your transmitter) are on.

A host name with a dot in it works only if it ends in `.localhost`, `.local`,
`.home.arpa` or `.internal`, or if you start `rigplane web` with
`--allowed-host <name>`; otherwise the server answers
`421 Misdirected Request`.

A private-network `http://` address is not a secure origin, so browser voice
TX needs HTTPS with a certificate the browser trusts: see
[Browser microphone requirement](web-ui.md#browser-microphone-requirement).

## Keep RigPlane Next to the Radio

Run RigPlane at the station, on the same network as a LAN radio, and use the
VPN or tunnel only between your browser and RigPlane. The radio's own LAN
audio can break over tunnels that drop IP fragments (see
[LAN Audio Breaks Over WireGuard or Other UDP Tunnels](troubleshooting.md#lan-audio-breaks-over-wireguard-or-other-udp-tunnels)),
and browser audio can stutter over a VPN with jitter (see
[Audio Stutters Over VPN/Tailscale](troubleshooting.md#audio-stutters-over-vpntailscale)).

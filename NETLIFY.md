# Publish the dashboards on Netlify

Netlify hosts the PAGES. The live data still comes from your laptop (SUMO runs there),
through a free Cloudflare tunnel. Keep the laptop, the simulation and the tunnel running during the demo.

## 1. Lock the control buttons (important - the site is public)
In settings.py set a secret word:
    CONTROL_KEY = "pune2026"
Without the key anyone can see the pages, but only you can press Pause / Approve / Give green.
The generated Netlify home page links to the public page and to the key-protected control page.

## 2. Start everything on the laptop
    Terminal 1:  python dashboard_server.py
    Terminal 2:  python both.py --predict

## 3. Open a tunnel to the laptop
Download cloudflared for Windows (Cloudflare's free tool, a single cloudflared.exe), then:
    Terminal 3:  cloudflared tunnel --url http://localhost:8600
It prints an address like  https://blue-river-1234.trycloudflare.com  (keep this terminal open).

## 4. Build the Netlify folder with that address
    python make_netlify_site.py https://blue-river-1234.trycloudflare.com
This creates the folder netlify_site.

## 5. Upload
Open https://app.netlify.com/drop (free account), drag the netlify_site folder onto the page.
Netlify gives you a link like https://traffic-demo-xyz.netlify.app

    Start page:                       https://traffic-demo-xyz.netlify.app/
    Public page (share / QR code):   https://traffic-demo-xyz.netlify.app/public.html
    Control room (only for you):     https://traffic-demo-xyz.netlify.app/control.html?key=pune2026

For the local server, authority accounts are separate from the Netlify key. Generate a PBKDF2
password hash with `python make_user.py <userid>` and paste its output into `settings.py` as
`AUTH_USERS`. If no accounts are configured, the local server starts with the temporary
`admin` / `admin123` account; replace it before sharing a local control room.

## Every new demo
The trycloudflare address changes each time you start the tunnel. Either run step 4 + 5 again
(drag the new folder onto your site's "Deploys" page), or simply add the new address to the link:
    https://traffic-demo-xyz.netlify.app/?api=https://new-address.trycloudflare.com

## If the page says it cannot reach the server
- dashboard_server.py or the tunnel is not running, or the address in the link is old.

# Simvoly embed

Paste this into a **Custom Code / HTML** element on the Simvoly page.

```html
<div id="smart1-ad-builder"></div>
<script>
(function () {
  var HOST = "https://adbuilder.onrender.com";
  var box  = document.getElementById("smart1-ad-builder");

  var frame = document.createElement("iframe");
  frame.src   = HOST + "/embed";
  frame.title = "Display Ad Request";
  frame.setAttribute("scrolling", "no");
  frame.style.cssText = "width:100%;border:0;display:block;min-height:600px";
  box.appendChild(frame);

  // The form tells the page how tall it is; without this the Simvoly block
  // keeps its fixed height and cuts the form off partway down.
  window.addEventListener("message", function (e) {
    if (e.source !== frame.contentWindow) return;
    if (e.origin !== HOST) return;
    var d = e.data || {};
    if (d.type === "smart1:height" && d.height > 0) frame.style.height = d.height + "px";
    if (d.type === "smart1:submitted") {
      frame.scrollIntoView({ behavior: "smooth", block: "start" });
      // Conversion tracking goes here, e.g.
      // if (window.gtag) gtag('event', 'generate_lead', { request_id: d.requestId });
    }
  });
})();
</script>
```

## If the embed is blank or 404s

Load `https://adbuilder.onrender.com/healthz` in a browser. It answers both
questions at once:

```json
"embed": {
  "available": true,          // false or 404 = deployed code predates /embed; redeploy
  "frameAncestors": "'self'", // must contain your site's origin
  "configured": false         // false = ALLOWED_FRAME_ANCESTORS was never set
}
```

- `available: false`, or the whole `embed` key missing, or `/healthz` 404s
  → Render is running an older commit. Push and redeploy.
- `configured: false` → the default only allows same-origin framing, so
  smart1marketing.com gets a blank box. Set the env var below and redeploy.

## Before it will work

**1. Allow the Simvoly domain to frame it.** Set this on the Render service, or
the browser blocks the iframe and the visitor sees an empty box with nothing but
a console error:

```
ALLOWED_FRAME_ANCESTORS = 'self' https://smart1marketing.com https://www.smart1marketing.com
```

Replace those with whatever hostname is in the browser address bar when you view
the live Simvoly page. If you are unsure, load the page, open the browser
console, and the block will name the origin it refused.
```

List every hostname the page is reachable at. `www` and the apex are different
origins as far as the browser is concerned, and Simvoly's editor preview may use
its own domain — check the address bar while editing and add that too.

**2. Serve over HTTPS.** A Simvoly page is HTTPS, so an HTTP iframe is mixed
content and gets blocked silently. Render terminates TLS automatically once the
custom domain is attached.

**3. Check the Render plan.** On the free plan the service sleeps after about
15 minutes of inactivity and takes roughly 50 seconds to wake. Embedded on a
marketing page that means the first visitor after a quiet spell stares at an
empty box and leaves. Use a paid instance type for anything customer-facing, or
keep it warm with a scheduled ping to `/healthz`.

## Notes

- `scrolling="no"` plus the height message is what avoids a scrollbar inside the
  form. Both are needed — remove either and you get a nested scroll area.
- The `e.origin !== HOST` check means a different site cannot spoof height
  messages at your page. Keep it.
- The form posts same-origin to `/api/requests`, so CORS is not involved. If you
  ever host the markup directly on the Simvoly page instead of in an iframe,
  set `ALLOWED_ORIGINS` to the site origin.
- Submissions currently land in `OUTPUT_DIR/requests/*.json` and are logged.
  Wiring them to HighLevel, Brandfetch and the confirmation email is still to do.

## Fallback if the iframe is blocked

Some site builders strip `<script>` from HTML blocks. If Simvoly does that,
the iframe alone still works, just at a fixed height:

```html
<iframe src="https://adbuilder.onrender.com/embed"
        title="Display Ad Request"
        style="width:100%;height:1600px;border:0;display:block"></iframe>
```

1600px fits the current form on desktop. It will be too tall on mobile and needs
revisiting whenever fields are added, which is why the script version is better.


## Monorepo notes

This project is one folder inside a repo that holds several Smart 1 projects.
That has three consequences worth knowing.

**Set Root Directory to `smart1-ad-builder`** in the Render service settings.
Without it, Render builds against the repo-root `package.json`, which belongs to
a different project.

**`smart1-ad-builder/render.yaml` is not read by Render.** Blueprints are only
loaded from the repository root. The live configuration is whatever is in the
Render dashboard; this file is a written record of it, nothing more.

**Do not sync the Blueprint from the repo root.** The root `render.yaml`
describes a different service. Syncing it can reconfigure or replace settings
you set by hand — including `ALLOWED_FRAME_ANCESTORS`, which would take the
embed down again.

**Build command:** use `npm install --include=dev && npm run build` unless
`package-lock.json` is committed. `npm ci` requires the lockfile and fails
without it.

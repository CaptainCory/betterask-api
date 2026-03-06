# DNS Setup — betterask.dev

## GoDaddy DNS Configuration

Domain registered: **betterask.dev** (GoDaddy, March 2026)

### Option A: Point to a VPS/Cloud Server

1. Go to [GoDaddy DNS Management](https://dcc.godaddy.com/manage/betterask.dev/dns)
2. Delete any existing A records for `@`
3. Add records:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | @ | `<your-server-ip>` | 600 |
| A | www | `<your-server-ip>` | 600 |
| CNAME | api | `<your-server-ip>` | 600 |

### Option B: Point to Railway/Render/Fly.io

1. In your hosting platform, add custom domain `betterask.dev`
2. They'll give you a CNAME target (e.g., `betterask-api.up.railway.app`)
3. In GoDaddy DNS:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| CNAME | @ | `<platform-target>` | 600 |
| CNAME | www | `<platform-target>` | 600 |

> **Note:** GoDaddy doesn't support CNAME on root (`@`) by default. Use their "forwarding" feature or switch to Cloudflare nameservers for CNAME flattening.

### Option C: Cloudflare (Recommended)

Best option for performance, SSL, and CNAME flattening:

1. Create free Cloudflare account → Add `betterask.dev`
2. Cloudflare gives you nameservers (e.g., `alice.ns.cloudflare.com`)
3. In GoDaddy → Domain Settings → Nameservers → Change to Custom:
   - `alice.ns.cloudflare.com`
   - `bob.ns.cloudflare.com`
4. In Cloudflare DNS, add your records pointing to your host
5. Enable "Proxied" (orange cloud) for CDN + SSL

### SSL

- **Cloudflare:** Automatic (Full Strict mode)
- **Direct VPS:** Use Let's Encrypt with certbot:
  ```bash
  certbot --nginx -d betterask.dev -d www.betterask.dev
  ```
- **Platform hosting (Railway/Render):** Automatic SSL

### Verification

```bash
# Check DNS propagation
dig betterask.dev +short
dig www.betterask.dev +short

# Check HTTPS
curl -I https://betterask.dev
```

DNS propagation takes 5 min–48 hours (usually <30 min with low TTL).

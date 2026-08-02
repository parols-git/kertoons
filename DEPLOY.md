# Deploying Kertoons to DigitalOcean

This app is a plain Python process (`server.py`, stdlib `http.server`, no
framework) that keeps its data as local files - `kertoons_data.json` (see
`story_engine/db.py`) and `generated/<job_id>/` (story images/text). That
means it needs **real, persistent disk and an always-on process** - a
DigitalOcean **Droplet** (a normal Ubuntu VM) is the right fit, not a
serverless/PaaS product with an ephemeral filesystem (those would silently
wipe your data on every redeploy or restart).

Everything below is written so you can follow it top to bottom on a brand
new Droplet and end up with the real app, running continuously, reachable
from the internet. Steps that need your DigitalOcean account/billing/SSH key
are yours to do - I can't create accounts or enter payment details on your
behalf. Where a step is just running commands, the exact commands are given.

**Already have kertoons.com running and just want to add this app at
`kertoons.com/story` instead of its own domain?** Skip to
["Mounting under an existing site"](#mounting-under-an-existing-site-kertoonscomstory)
near the bottom - it reuses steps 3-7 below (Python setup, systemd service)
but replaces the standalone nginx config with a location block added to your
existing site.

**Cost**: the cheapest "Basic" Droplet (1 vCPU / 1GB RAM / 25GB SSD) is
enough for this app and currently runs about $6/month, billed hourly. A
domain name is optional (~$10-15/year if you want one instead of using the
bare IP address). Your OpenAI/Gemini/DeepAI API usage is billed separately by
those providers, same as it is today running locally.

---

## 1. Create the Droplet

1. Go to https://www.digitalocean.com and create an account (or log in).
2. Click **Create → Droplets**.
3. **Image**: Ubuntu, latest LTS (24.04 as of writing).
4. **Plan**: Basic → Regular SSD → the $6/mo (1GB RAM / 1 vCPU) tier is fine
   to start; you can resize later without reinstalling anything.
5. **Datacenter region**: pick whichever is closest to you or your users.
6. **Authentication**: choose **SSH Key**, not password - click "New SSH
   Key" and paste your public key (on Windows, `type $env:USERPROFILE\.ssh\id_ed25519.pub`
   in PowerShell if you already have one; otherwise run
   `ssh-keygen -t ed25519` first to generate one).
7. **Hostname**: anything you like, e.g. `kertoons`.
8. Click **Create Droplet**. After a minute you'll have a public IPv4
   address - that's `YOUR_DROPLET_IP` for every command below.

## 2. First login and basic server setup

From your Windows machine (PowerShell or Git Bash both work):

```bash
ssh root@YOUR_DROPLET_IP
```

Once connected, update the system and create a dedicated non-root user to
run the app as (never run app code as root):

```bash
apt update && apt upgrade -y
adduser --disabled-password --gecos "" kertoons
usermod -aG sudo kertoons
```

Set up the firewall (only SSH, HTTP, HTTPS need to be reachable):

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

## 3. Install Python, nginx, and (optionally) certbot

Still as root:

```bash
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

## 4. Get the app code onto the Droplet

This project isn't in a git repo, so the simplest path is copying it
directly from your Windows machine. From a **new terminal on your Windows
machine** (not the SSH session):

```bash
scp -r "C:/claude_code/kertoons/kertoons-app" root@YOUR_DROPLET_IP:/opt/kertoons-app
```

This copies everything, including `.env` (with your real keys) and the
current `kertoons_data.json`/`generated/` if you want to bring existing data
along. If you'd rather start with a clean slate on the server, delete
`kertoons_data.json` and empty `generated/` locally before running `scp`, or
just remove them on the server afterward (`rm` the file, `rm -rf` the
contents of `generated/`) - `server.py` recreates both automatically on
first use.

*(If you'd prefer a repeatable, versioned deploy instead of one-off `scp`:
push this folder to a private GitHub repo first, then on the Droplet run
`git clone <your-repo-url> /opt/kertoons-app`. Same result, easier to update
later with `git pull`.)*

Back in your SSH session, fix ownership so the `kertoons` user can run it:

```bash
chown -R kertoons:kertoons /opt/kertoons-app
```

## 5. Python environment and dependencies

```bash
su - kertoons
cd /opt/kertoons-app
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
exit   # back to root/sudo user
```

## 6. Configure environment variables

If you didn't copy an existing `.env` in step 4, create one from the
template:

```bash
su - kertoons -c "cp /opt/kertoons-app/.env.example /opt/kertoons-app/.env"
nano /opt/kertoons-app/.env
```

Fill in `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPAI_API_KEY` with your real
keys (leave any blank to run that part in mock mode). Leave `HOST=127.0.0.1`
and `PORT=8765` - nginx (next step) is what the public internet actually
talks to; the app itself should never be directly reachable.

Also set `ADMIN_USERNAME`/`ADMIN_PASSWORD` here to get an admin account
auto-created on first start (see the "Admin panel" section of README.md) -
otherwise `/admin.html` stays permanently inaccessible (no account will ever
have `role: "admin"`).

## 7. Run it as a persistent service (systemd)

The repo includes a ready-made unit file at `deploy/kertoons.service`:

```bash
cp /opt/kertoons-app/deploy/kertoons.service /etc/systemd/system/kertoons.service
systemctl daemon-reload
systemctl enable --now kertoons
systemctl status kertoons   # should show "active (running)"
```

This makes the app start on boot and auto-restart if it ever crashes. To
watch its logs live: `journalctl -u kertoons -f`.

## 8. Reverse proxy with nginx

The repo also includes `deploy/nginx_kertoons.conf`. Copy it, then edit the
`server_name` line to your Droplet's IP (or your domain, once you have one):

```bash
cp /opt/kertoons-app/deploy/nginx_kertoons.conf /etc/nginx/sites-available/kertoons
nano /etc/nginx/sites-available/kertoons   # replace YOUR_DOMAIN_OR_IP
ln -s /etc/nginx/sites-available/kertoons /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default   # remove nginx's default placeholder site
nginx -t && systemctl reload nginx
```

At this point, **the app is live** at `http://YOUR_DROPLET_IP` - open it in
a browser, register an account, and create a story to confirm everything
works end to end.

## 9. Optional: a real domain name + free HTTPS

If you own a domain (or buy one from any registrar), point an `A` record at
it to `YOUR_DROPLET_IP`, wait for DNS to propagate (a few minutes to an
hour), update `server_name` in `/etc/nginx/sites-available/kertoons` to the
domain instead of the IP, `nginx -t && systemctl reload nginx`, then run:

```bash
certbot --nginx -d yourdomain.com
```

Certbot edits the nginx config in place to add a free Let's Encrypt
certificate and auto-renewal, and offers to redirect HTTP to HTTPS - say yes.
Your site is now `https://yourdomain.com`.

---

## Mounting under an existing site (`kertoons.com/story`)

If `kertoons.com` is already live on nginx (e.g. from following steps 1-3
above previously) and you want to add this app at `kertoons.com/story`
**instead of** giving it the whole domain, do this instead of steps 4, 6, 8,
and 9:

**Every internal link, script/style reference, and API call in this app is
relative** (`static/nav.js`, `api/story/view?...`, etc. - never a leading
`/`), specifically so it works correctly no matter what path prefix it's
served under. You don't need to configure a "base path" anywhere in the
app itself - nginx stripping the `/story/` prefix before forwarding is the
only piece that needs to know about it.

1. **Get the code onto the server**, same as step 4, but into its own
   directory alongside your existing site rather than replacing it:
   ```bash
   # from Windows:
   scp -r "C:/claude_code/kertoons/kertoons-app" root@YOUR_SERVER_IP:/opt/kertoons-app
   # on the server:
   chown -R kertoons:kertoons /opt/kertoons-app   # create the "kertoons" user first if you haven't (step 2)
   ```

2. **Python environment, dependencies, and `.env`** - identical to steps 5
   and 6 above (a venv, `pip install -r requirements.txt`, fill in your API
   keys; leave `HOST=127.0.0.1` and `PORT=8765` unless that port is already
   taken by something else on this server, in which case pick a free one and
   use the same value in step 4 below).

3. **systemd service** - identical to step 7 above
   (`deploy/kertoons.service`, `systemctl enable --now kertoons`). This app
   runs as its own independent process regardless of URL path - only the
   nginx routing in front of it changes.

4. **nginx**: instead of `deploy/nginx_kertoons.conf` (which owns the whole
   `server {}` block), use `deploy/nginx_kertoons_subpath.conf` - open your
   **existing** kertoons.com site config
   (probably `/etc/nginx/sites-available/kertoons.com` or similar - run
   `nginx -T | grep -B5 "server_name kertoons.com"` if you're not sure which
   file it's in), and paste the two `location` blocks from that file inside
   the existing `server { ... }` block (anywhere alongside its other
   `location` blocks - order relative to a `location /` block for the main
   site doesn't matter here, since `/story/` is more specific and nginx
   always prefers the longest matching prefix).
   ```bash
   nano /etc/nginx/sites-available/kertoons.com   # paste the two location blocks in
   nginx -t && systemctl reload nginx
   ```

5. **Verify**: open `https://kertoons.com/story/` (note the trailing slash -
   `https://kertoons.com/story` without it will redirect there
   automatically) and confirm the existing `kertoons.com` site is completely
   unaffected.

If you'd rather run the app on a **different** machine than the one serving
kertoons.com, the only change is `proxy_pass` in
`deploy/nginx_kertoons_subpath.conf` - point it at that machine's address
(`http://OTHER_SERVER_IP:8765/`) instead of `127.0.0.1:8765/`, and make sure
that machine's firewall allows inbound traffic on port 8765 from the
kertoons.com server's IP specifically (not the open internet).

---

## Selling credits with Stripe

"Add credits" sells one pack - 50 image credits for $5 - through **Stripe
Checkout**, a payment page hosted entirely on Stripe's own domain. This app
never receives, sees, or stores a card number; it only ever gets Stripe's
confirmation that a specific payment succeeded. Creating the actual Stripe
account is something only you can do (it needs your own business/bank
details for payouts) - I can't do that step for you. Everything else below
is just configuration.

1. **Create a Stripe account** at https://dashboard.stripe.com/register if
   you don't have one.
2. **Get your API key**: https://dashboard.stripe.com/apikeys - copy the
   **Secret key**. Start with the one shown while "Test mode" is toggled on
   (top-right of the dashboard) - it starts with `sk_test_...` and lets you
   run the entire flow, including a real-looking checkout page, without any
   real money moving, using Stripe's documented test card `4242 4242 4242
   4242` (any future expiry date, any 3-digit CVC, any ZIP).
3. **Register a webhook endpoint**: https://dashboard.stripe.com/webhooks →
   "Add endpoint".
   - Endpoint URL: `https://yourdomain.com/api/stripe/webhook` (or
     `https://yourdomain.com/story/api/stripe/webhook` if mounted under an
     existing site's `/story` path).
   - Events to send: select **`checkout.session.completed`** (only that one
     is needed).
   - After creating it, click into the endpoint and copy its **Signing
     secret** (starts with `whsec_...`).
4. **Set the three new variables** in `/opt/kertoons-app/.env` on the
   server:
   ```
   STRIPE_SECRET_KEY=sk_test_...          # or sk_live_... once you're ready for real payments
   STRIPE_WEBHOOK_SECRET=whsec_...
   PUBLIC_BASE_URL=https://yourdomain.com  # include /story if mounted under an existing site
   ```
   Then restart the service so it picks them up: `systemctl restart kertoons`.
5. **Test it**: log in, click "Add credits" - you should land on a real
   Stripe-hosted checkout page showing "50 Kertoons image credits - $5.00".
   Pay with the test card above, and you should be redirected back with a
   "Payment received" banner and your credit balance increased by 50
   immediately (confirmed both by the browser's return trip AND,
   independently, by Stripe's webhook - see `story_engine/payments.py` for
   why both exist and can't double-credit you).
6. **Go live**: once you're satisfied testing, toggle "Test mode" off in the
   Stripe dashboard, get the **live** secret key and a **live** webhook
   signing secret (steps 2-3 again, but for live mode - these are separate
   from the test-mode ones), and swap `STRIPE_SECRET_KEY`/
   `STRIPE_WEBHOOK_SECRET` in `.env` to the live values.

**What this app never does**: collect card numbers directly, store payment
details, or process a charge itself - Stripe's hosted page handles all of
that. If you ever want a different price or pack size, both live as
constants at the top of `story_engine/payments.py`
(`CREDIT_PACK_CREDITS`, `CREDIT_PACK_PRICE_USD_CENTS`).

---

## Updating the app later

Whenever you make changes locally and want to push them live:

```bash
# from Windows:
scp -r "C:/claude_code/kertoons/kertoons-app/story_engine" root@YOUR_DROPLET_IP:/opt/kertoons-app/
scp -r "C:/claude_code/kertoons/kertoons-app/static" root@YOUR_DROPLET_IP:/opt/kertoons-app/
scp "C:/claude_code/kertoons/kertoons-app/server.py" root@YOUR_DROPLET_IP:/opt/kertoons-app/
# then on the Droplet:
ssh root@YOUR_DROPLET_IP "chown -R kertoons:kertoons /opt/kertoons-app && systemctl restart kertoons"
```

(Deliberately not re-copying `.env`, `kertoons_data.json`, or `generated/` -
those hold live secrets/data you don't want to overwrite from your local
copy.) If you set up the git-based deploy instead, this is just
`git pull && systemctl restart kertoons` on the Droplet.

## Backups

Everything that matters is in two places: `/opt/kertoons-app/kertoons_data.json`
(accounts/sessions/story ownership) and `/opt/kertoons-app/generated/`
(story content). Neither is backed up automatically. Easiest options:

- DigitalOcean's built-in Droplet **Backups** (enable in the Droplet's
  settings, ~20% of the Droplet's monthly cost) - whole-server weekly
  snapshots, no setup needed.
- Or periodically pull a copy down yourself:
  `scp -r root@YOUR_DROPLET_IP:/opt/kertoons-app/kertoons_data.json .` and
  same for `generated/`.

## Security notes worth knowing

- Story creation already requires a logged-in account (see the accounts
  system), so a random visitor can't burn your OpenAI/Gemini/DeepAI credits
  without registering first - but any registered user can. If you're
  deploying this publicly rather than just for yourself, keep an eye on API
  usage/billing on each provider's dashboard.
- Session cookies are `HttpOnly` but not marked `Secure` (see
  `_session_cookie_header` in `server.py`) - fine over plain HTTP, but once
  you've set up HTTPS (step 9) it's worth adding `; Secure` to that cookie
  header so it's never sent over an unencrypted connection. Ask if you'd
  like this made conditional on HTTPS being active.
- `kertoons_data.json` and `generated/` are not reachable via any web route
  (`server.py` only serves `static/` and specific API endpoints), so they're
  not exposed even though they live on the same disk as the app.
- `/api/stripe/webhook` requires no login (Stripe itself calls it, not a
  logged-in browser) - it's protected instead by verifying Stripe's request
  signature against `STRIPE_WEBHOOK_SECRET` (see
  `payments.verify_webhook_signature`); a request without a valid signature
  is rejected outright and never touches account balances. Keep
  `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` as secret as any other
  credential in `.env`.

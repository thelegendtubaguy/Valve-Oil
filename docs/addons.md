# Add-ons

[← Back to the docs](README.md)

Add-ons are optional programs that Valve Oil installs, updates, and removes for you. Each one runs from its own folder. OctoEverywhere and Obico come with their own copy of the Python packages they need, which ship with Valve Oil. Tailscale and ZeroTier are downloaded from Tailscale and ZeroTier when you install them. Installing an add-on doesn't install system packages, doesn't change files in `printer_data/config`, and doesn't turn on Moonraker's update manager. QIDI firmware updates leave add-ons and their settings in place.

The add-ons are [OctoEverywhere](#octoeverywhere), [Obico](#obico), [Tailscale](#tailscale), and [ZeroTier](#zerotier).

OctoEverywhere and Obico both give remote access to Fluidd and the camera, print notifications, and AI failure detection, so only one of them can be installed at a time. To switch, remove the one you have first. Fluidd's **Install** for the other says which one to remove.

Tailscale and ZeroTier each put the printer on your own private network, so your devices reach it from anywhere as if they were at home. Either or both can be installed alongside OctoEverywhere or Obico.

## Installing from Fluidd

Open Fluidd's **Settings** and go to **Add-ons**. Each add-on has an **Install** button. Installing takes about a minute and shows its progress, with the full log one click away. **Remove** asks first, then stops the add-on and deletes it with its settings.

Add-ons can be installed, updated, or relinked only while the printer isn't printing or running G-code such as a calibration, since building one takes CPU time and memory those need. A heater that is on doesn't stop it. One change runs at a time, and it keeps running if you close the page.

Fluidd's **Add-ons** section talks to the printer it was opened from. Moonraker decides who may use it, the same way it does for Fluidd itself.

## Updating

Each Valve Oil release pins the version of every add-on it offers. When you install or update Valve Oil, including through automatic updates, installed add-ons move to that release's version. If an add-on's new version doesn't keep running, or Tailscale's or ZeroTier's new version can't be downloaded, the version you had stays and the install says so. Valve Oil also says when an add-on update fails, and the rest of the update still goes ahead.

## From SSH

`~/valve-oil/addons.sh` does the same from the command line:

```bash
~/valve-oil/addons.sh list
~/valve-oil/addons.sh install octoeverywhere
~/valve-oil/addons.sh update octoeverywhere
~/valve-oil/addons.sh remove octoeverywhere
~/valve-oil/addons.sh relink obico --server http://192.168.1.10:3334
~/valve-oil/addons.sh install tailscale
~/valve-oil/addons.sh install zerotier
```

`relink` is for Obico. See [Changing the server or unlinking](#changing-the-server-or-unlinking).

Fluidd uses QIDI's default sudo password to install add-ons. If you've changed the `qidi` user's password, Fluidd's install fails and says so. Run the command above over SSH instead, and enter your password when asked.

## OctoEverywhere

[OctoEverywhere](https://octoeverywhere.com) gives remote access to Fluidd and the camera, print notifications, and AI failure detection from anywhere.

### Linking your account

After installing, the OctoEverywhere panel under **Add-ons** shows whether it's connected and whether an account is linked. To link it, click **Link account** and sign in to OctoEverywhere. To link from another device, click **Show link code** and enter the code at [octoeverywhere.com/code](https://octoeverywhere.com/code) before it expires. Within about half a minute of linking, the panel says **Linked**. To unlink the printer, remove it from your OctoEverywhere dashboard.

### What it changes

OctoEverywhere's own installer is written for many kinds of printer. On a QIDI printer it also installs system packages, removes the `ntp` and `ntpdate` packages, rewrites the clock settings, and adds itself to Moonraker's update manager. That last change turns on Moonraker's update panel in Fluidd, which then offers to upgrade the printer's Debian packages, and QIDI warns against using that panel after installing OctoEverywhere. Valve Oil's install does none of that. It installs:

- the OctoEverywhere release and its packages in `~/tltg-addons/octoeverywhere/`;
- its settings and identity in `~/tltg-addons/octoeverywhere-data/`;
- the `octoeverywhere` service, running on CPUs 0 and 3, away from Klipper's CPUs, at a lower priority than Klipper.

Its log is `octoeverywhere.log` in Fluidd's **Logs**. Fluidd's power menu lists the `octoeverywhere` service with **Restart**, **Stop**, and **Start**. Valve Oil sets OctoEverywhere's remote access to open the Fluidd on port 80. OctoEverywhere uses about 55 MB of the printer's memory. During a print it also uses about 14% of one CPU core, mostly for the camera snapshots it takes every few seconds while printing.

Removing OctoEverywhere deletes its identity, so installing it again later gives a new printer to link. If you installed OctoEverywhere with its own installer, Fluidd shows it as installed outside Valve Oil and leaves it alone.

## Obico

[Obico](https://www.obico.io) gives remote access to Fluidd and the camera, print notifications, and AI failure detection, through Obico's cloud or an Obico server you run yourself.

### Linking your account

Obico starts out using Obico Cloud. To use your own server, change the server first, as below. Once installed, Obico waits up to 4 hours to be linked, and its panel under **Add-ons** shows **Not linked** and a link code. To link it, add a printer in the Obico app or at [app.obico.io](https://app.obico.io), or on your own Obico server, then:

1. For the device, select **Generic Klipper - Self Installed**.
2. Select **Switch to Manual Linking**.
3. Enter the code from the panel.

Within a few seconds of linking, the panel says **Linked**, with a link to the Obico server. If 4 hours pass without linking, the panel says Obico stopped waiting. Click **Restart linking** to start it waiting again, with a new code.

### Changing the server or unlinking

Click **Change** next to the server to choose Obico Cloud or your own Obico server, such as `http://192.168.1.10:3334`. **Unlink** removes the printer from your account. Both restart Obico, and it waits to be linked again. To remove the printer from your Obico account completely, also delete it in the Obico app.

### What it changes

Obico's own installer also installs system packages, adds itself to Moonraker's update manager, keeps its settings in `printer_data/config`, and adds `[include moonraker_obico_macros.cfg]` to `printer.cfg`. Valve Oil's install does none of that. It installs:

- the Obico release and its packages in `~/tltg-addons/obico/`;
- its settings and account link in `~/tltg-addons/obico-data/`;
- the `moonraker-obico` service, running on CPUs 0 and 3, away from Klipper's CPUs, at a lower priority than Klipper.

Its log is `moonraker-obico.log` in Fluidd's **Logs**. Fluidd's power menu lists the `moonraker-obico` service once Moonraker has restarted. Valve Oil sets Obico's remote access to open the Fluidd on port 80, and keeps the server you chose when Obico is updated.

Obico streams the camera with the WebRTC server it ships for Raspberry Pi's Debian 11, which runs on QIDI's Debian 11. Out of the box it looks for one made for the printer's board and, finding none, streams no video. On a board without a video encoder, such as QIDI's, Obico streams by sending the camera's full-size snapshots, and it does that all the time, whether or not anyone is watching. Valve Oil's Obico sends them only while someone has the printer open in Obico, and for a minute after. Obico's first-layer scan needs a nozzle camera and its macros in `printer.cfg`, so it is not used.

On the test Max 4, a linked Obico that nobody is watching uses about 4% of one CPU core and 57 MB of memory, its WebRTC server included. Streaming all the time, as Obico does by itself, took about 30% of one core. While it waits to be linked, Obico checks in with the server every 2 seconds for its link code, which takes about 11% of one core, and answers on port 46793, which the Obico app uses to find printers on its network.

Removing Obico deletes its settings and account link. If you installed Obico with its own installer, Fluidd shows it as installed outside Valve Oil and leaves it alone.

## Tailscale

[Tailscale](https://tailscale.com) joins the printer to your tailnet, the private network between your own devices. Any device on your tailnet can then open Fluidd, watch the camera, and use SSH from anywhere, at the printer's Tailscale name or address. Nothing is opened to the internet, and you need a Tailscale account, which is free for personal use.

### Installing

Installing Tailscale needs the printer to be online. Valve Oil downloads Tailscale 1.104.6 for the printer, a 38 MB file, from `pkgs.tailscale.com`, and installs it only if it is exactly the file this Valve Oil release names. It takes about a minute.

### Logging in

After installing, the Tailscale panel under **Add-ons** says **Not logged in**. Click **Log in**. The panel shows a sign-in address and a **Sign in to Tailscale** button. Open it on any device, sign in to Tailscale, and approve the printer. **New link** gives a fresh address and makes the earlier one useless, so sign in with the newest one.

Within about 10 seconds of signing in, the panel says **Connected** and shows your tailnet, the printer's name and addresses on it, and when its sign-in expires. The name and address are links that open the printer's Fluidd over Tailscale. From another device on your tailnet, open `http://<the printer's Tailscale name or address>/`.

Tailscale signs devices out after 180 days unless you turn off key expiry for the printer in Tailscale's admin console. When that happens the panel says **Not logged in** again, and **Log in** brings it back.

**Log out** takes the printer off your tailnet. It stays listed in Tailscale's admin console, signed out, until you remove it there. If your tailnet approves new devices by hand, the panel says the printer is waiting for approval until you approve it in the admin console.

### What tailnet devices can reach

Connections from your tailnet arrive at the printer as if they came from the printer itself. That means:

- Fluidd and Moonraker treat every device on your tailnet as trusted, the same as a device on your home network, with no login.
- Every service on the printer answers, SSH included, and so do the ones that otherwise answer only to the printer itself.

Add the printer only to a tailnet whose devices and people you'd let use it. To narrow who can reach it, use [Tailscale's access controls](https://tailscale.com/kb/1018/acls), or share only this printer with someone instead of inviting them to your tailnet.

The printer's own programs can't open connections to other devices on your tailnet. Only connections coming in to the printer work.

### What it changes

Tailscale's own packages install system-wide and expect a tunnel device, firewall, and routing features that QIDI's Linux kernel doesn't have, so they don't work on a QIDI printer. Valve Oil runs Tailscale's own programs in their userspace networking mode, which needs none of those, as the `qidi` user instead of root. It changes no routes, firewall rules, or DNS settings. It installs:

- Tailscale's `tailscaled` and `tailscale` programs, unmodified, in `~/tltg-addons/tailscale/`;
- the printer's Tailscale identity and settings in `~/tltg-addons/tailscale-data/`;
- the `tailscaled` service, running on CPUs 0 and 3, away from Klipper's CPUs, at a lower priority than Klipper.

On the test Plus 5, a connected Tailscale that nobody is using takes about 34 MB of memory and under 0.5% of one CPU core. Watching the camera over Tailscale took about 27% of one core, since Tailscale encrypts everything it carries. Devices on the same home network as the printer connect to it directly, and Tailscale relays through its servers only when no direct path exists.

Its log is in the system journal. Read it with `journalctl -u tailscaled` over SSH. To use Tailscale's command line, name its socket:

```bash
~/tltg-addons/tailscale/app/tailscale --socket=$HOME/tltg-addons/tailscale-data/tailscaled.sock status
```

Removing Tailscale logs the printer out of your tailnet and deletes its identity, so installing it again later means logging in again, and it joins as a new machine. If you installed Tailscale some other way, Fluidd shows it as installed outside Valve Oil and leaves it alone.

## ZeroTier

[ZeroTier](https://www.zerotier.com) joins the printer to a ZeroTier network you create in ZeroTier Central at [my.zerotier.com](https://my.zerotier.com). Any device on that network can then open Fluidd, watch the camera, and use SSH from anywhere, at the printer's address on the network. Nothing is opened to the internet.

You need a ZeroTier account. ZeroTier's licence lets you use the build Valve Oil installs free of charge only for personal, non-commercial use. Use by or for a company, a non-profit, or a government, or for business purposes, needs a paid ZeroTier plan or licence; see ZeroTier's [licence](https://github.com/zerotier/ZeroTierOne/blob/main/nonfree/LICENSE.md).

### Installing

Installing ZeroTier needs the printer to be online. Valve Oil downloads ZeroTier 1.16.2's Debian 11 package for the printer, a 2.8 MB file, from `download.zerotier.com`, and installs it only if it is exactly the file this Valve Oil release names. It takes under a minute.

ZeroTier needs Linux's `tun` driver, which QIDI's kernel doesn't include, so Valve Oil brings its own build of it, made for the kernels of the Max 4, Plus 5, and Smart 5 firmware Valve Oil supports. The install is refused on a printer whose kernel is a build the driver isn't made for, and if a QIDI firmware update later brings a different kernel, ZeroTier doesn't start until a Valve Oil release supports it.

### Joining a network

1. In ZeroTier Central, create a network, or open one you have, and copy its 16-character **Network ID**.
2. In the ZeroTier panel under **Add-ons**, enter the network ID and click **Join**.
3. The panel says **Waiting for approval** and shows the printer's **Node ID**. In ZeroTier Central, open the network's **Members**, find that node ID, and tick **Auth**.

Within a few seconds of authorizing it, the panel says **Connected** and shows the printer's addresses on the network. The address is a link that opens the printer's Fluidd over ZeroTier. From another device on the network, open `http://<the printer's ZeroTier address>/`.

The printer can be on more than one network. ZeroTier remembers its networks across restarts and Valve Oil updates. **Leave** takes the printer off a network. It stays in the network's member list in ZeroTier Central until you delete it there.

### What network devices can reach

The printer gets a network connection on each ZeroTier network it joins, as it has on your home network. Devices on the network reach every service the printer offers to your home network, SSH included, and the printer's own programs can reach devices on the network.

Fluidd and Moonraker treat devices on the network as they treat your home network. QIDI's Moonraker trusts the private address ranges `10.0.0.0/8`, `172.16.0.0/12`, and `192.168.0.0/16` without a login, and ZeroTier Central's automatic address ranges are all in them. On a network you've given addresses outside those ranges, Fluidd can't connect to the printer over ZeroTier.

ZeroTier takes the addresses and routes the network gives it, but not a default route or DNS settings, so the printer's own internet connection doesn't change.

Join the printer only to networks whose devices and people you'd let use it. To narrow who can reach it, use ZeroTier's [flow rules](https://docs.zerotier.com/rules/).

### What it changes

ZeroTier's own package installs system-wide and runs as root. Valve Oil installs:

- ZeroTier's `zerotier-one` program, unmodified, in `~/tltg-addons/zerotier/`;
- the printer's ZeroTier identity and its networks in `~/tltg-addons/zerotier-data/`;
- the `tun` driver and the script that loads it in `/usr/local/lib/tltg-addons/zerotier/`, owned by root, since root loads them;
- the `zerotier-one` service, which loads the `tun` driver as root and then runs ZeroTier as the `qidi` user with only the rights it needs to create and configure its network connection, on CPUs 0 and 3, away from Klipper's CPUs, at a lower priority than Klipper.

On the test Plus 5, ZeroTier takes about 12 MB of memory and under 1% of one CPU core while nothing uses it.

Its log is in the system journal. Read it with `journalctl -u zerotier-one` over SSH. To use ZeroTier's command line, name its folder:

```bash
~/tltg-addons/zerotier/app/zerotier-one -q -D$HOME/tltg-addons/zerotier-data listnetworks
```

Removing ZeroTier unloads the `tun` driver and deletes the printer's ZeroTier identity, so installing it again later gives the printer a new node ID, which you authorize again. If you installed ZeroTier some other way, Fluidd shows it as installed outside Valve Oil and leaves it alone.

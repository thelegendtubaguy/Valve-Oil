# Add-ons

[← Back to the docs](README.md)

Add-ons are optional programs that Valve Oil installs, updates, and removes for you. Each one runs from its own folder with its own copy of the Python packages it needs, which ship with Valve Oil. Installing one doesn't install system packages, doesn't change files in `printer_data/config`, and doesn't turn on Moonraker's update manager. QIDI firmware updates leave add-ons and their settings in place.

The first add-on is [OctoEverywhere](#octoeverywhere).

## Installing from Fluidd

Open Fluidd's **Settings** and go to **Add-ons**. Each add-on has an **Install** button. Installing takes about a minute and shows its progress, with the full log one click away. **Remove** asks first, then stops the add-on and deletes it with its settings.

Add-ons can be installed or updated only while the printer isn't printing. One change runs at a time, and it keeps running if you close the page.

Fluidd's **Add-ons** section talks to the printer it was opened from. Moonraker decides who may use it, the same way it does for Fluidd itself.

## Updating

Each Valve Oil release pins the version of every add-on it offers. When you install or update Valve Oil, including through automatic updates, installed add-ons move to that release's version. If an add-on's new version doesn't keep running, the version you had is put back and the install says so. Valve Oil also says when an add-on update fails, and the rest of the update still goes ahead.

## From SSH

`~/tltg-optimized-macros/addons.sh` does the same from the command line:

```bash
~/tltg-optimized-macros/addons.sh list
~/tltg-optimized-macros/addons.sh install octoeverywhere
~/tltg-optimized-macros/addons.sh update octoeverywhere
~/tltg-optimized-macros/addons.sh remove octoeverywhere
```

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

Its log is `octoeverywhere.log` in Fluidd's **Logs**. Fluidd's power menu lists the `octoeverywhere` service with **Restart**, **Stop**, and **Start**. Valve Oil sets OctoEverywhere's remote access to open the Fluidd on port 80. OctoEverywhere uses about 50 MB of the printer's memory.

Removing OctoEverywhere deletes its identity, so installing it again later gives a new printer to link. If you installed OctoEverywhere with its own installer, Fluidd shows it as installed outside Valve Oil and leaves it alone.

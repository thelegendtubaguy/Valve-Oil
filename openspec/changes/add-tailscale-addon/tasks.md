## 1. Package and runtime

- [x] 1.1 Let an add-on be pinned as a download without wheels; keep it out of the bundle; teach `scripts/lock_addon.py` to pin it.
- [x] 1.2 Download a pinned add-on on install and update, verify it before extracting, and skip the Python environment when it has no wheels.
- [x] 1.3 Add the Tailscale definition and unit, and the logout before removal.
- [x] 1.4 Add Tailscale's status, log in, and log out to the add-ons service.
- [x] 1.5 Extend the add-on lifecycle tests.

## 2. Fluidd

- [x] 2.1 Add the Tailscale panel to the fork.
- [x] 2.2 Release the fork and pin the build in `installer/package.yaml`.

## 3. Validation on the test Plus 5

- [x] 3.1 Run `tailscaled` in userspace mode as `qidi` on QIDI's kernel and get a sign-in address.
- [x] 3.2 Install, remove, and reinstall through `addons.sh` and Fluidd; get a sign-in address from the panel.
- [ ] 3.3 Sign in to a tailnet; open Fluidd, the camera, and SSH from another tailnet device.
- [ ] 3.4 Log out from Fluidd; measure a connected Tailscale idle.

## 4. Docs

- [x] 4.1 `docs/addons.md`, README, settings, under-the-hood, changelog, and third-party notices.

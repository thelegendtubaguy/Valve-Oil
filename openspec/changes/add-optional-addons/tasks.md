## 1. Package and runtime

- [x] 1.1 Pin OctoEverywhere 5.4.1, its 23 wheels for Python 3.9 aarch64, and pip in `installer/package.yaml`; add `scripts/lock_addon.py`.
- [x] 1.2 Download and verify the add-on files in the bundle builder; ship `addons.sh`.
- [x] 1.3 Install, update with rollback, and remove add-ons; refuse installs made outside the package; update installed add-ons on install and remove them on uninstall.
- [x] 1.4 Add the add-ons service, its unit, and the `/tltg-addons/` route, authorized through Moonraker, with POSTs limited to same-origin JSON requests carrying `X-Valve-Oil: addons`.
- [x] 1.5 Lifecycle tests for add-ons and the service's request checks; extend the host lifecycle test.

## 2. Fluidd

- [x] 2.1 Add the **Add-ons** settings section and the OctoEverywhere panel to the Fluidd fork.
- [x] 2.2 Release the fork and pin the build in `installer/package.yaml`.

## 3. Validation on the test Max 4

- [x] 3.1 Install, reinstall, remove, and install again through `addons.sh` and the service; a failed job reports its error.
- [x] 3.2 The route answers trusted clients, refuses untrusted ones with 401 and cross-origin POSTs with 403, and reaches Moonraker's `/access/user` as GET.
- [ ] 3.3 Install the release bundle end to end and use the Add-ons section from Fluidd.
- [ ] 3.4 Measure OctoEverywhere's memory during a print.

## 4. Docs

- [x] 4.1 `docs/addons.md`, README, settings, under-the-hood, Fluidd, changelog, and third-party notices.

## 1. Package and runtime

- [x] 1.1 Pin moonraker-obico 2.2.4, its wheels, and its two source-only packages; teach `scripts/lock_addon.py` to pin PyPI sdists.
- [x] 1.2 Build pinned sdists offline after the wheels; extract same-directory symlinks as copies; add package files to an add-on's source and its build id.
- [x] 1.3 Add the Obico definition and launcher, enforced and default settings, exclusive remote-access add-ons, and `relink` with server validation.
- [x] 1.4 Add Obico's status and link code to the add-ons service, and relink jobs with a server option.
- [x] 1.5 Extend the add-on lifecycle and service request tests.

## 2. Fluidd

- [x] 2.1 Add the Obico panel, server change, unlink, and the blocked-install hint to the fork.
- [ ] 2.2 Release the fork and pin the build in `installer/package.yaml`.

## 3. Validation on the test Max 4

- [x] 3.1 Build the environment offline and run the launcher unlinked: link code reported, `printer.cfg` and the config folder unchanged.
- [x] 3.2 Install, relink to another server, remove, and reinstall through `addons.sh` and Fluidd; installing with OctoEverywhere present is refused.
- [x] 3.3 Link an account and stream the camera through Janus, only while viewed.
- [ ] 3.4 Measure Obico's memory and CPU during a print.

## 4. Docs

- [ ] 4.1 `docs/addons.md`, README, settings, under-the-hood, changelog, and third-party notices.

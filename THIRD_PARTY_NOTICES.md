# Third-Party Notices

Except for the third-party material identified below, repository-authored content is licensed under the GNU General Public License version 3 only (`GPL-3.0-only`) in [`LICENSE`](LICENSE).

`installer/system/qidiclient-static-gifs.tar.gz` was authored by the project maintainer and is covered by the repository license.

## GPL-3.0-only components

The full GPLv3 text is available in [`LICENSE`](LICENSE) and [`LICENSES/GPL-3.0-only.txt`](LICENSES/GPL-3.0-only.txt).

### Klipper

- Source: <https://github.com/Klipper3d/klipper>
- License: GPL-3.0-only
- Paths:
  - `installer/klipper/qidi/homing.py`
  - `installer/klipper/qidi/homing-sync-reset.py`
  - `installer/tests/fixtures/source-patches/01.01.06.04/homing-sync-reset.py`
  - `installer/klipper/extras/resonance_sweep.py` (adapted portions)
  - `installer/system/klipper_chelper/*.so`
  - `chelper/*.patch`
  - `klipper/` (upstream Klipper v0.14.0 with QIDI's Klipper changes and Valve Oil's port)

The installer payloads are modified QIDI/Klipper homing sources. `homing.py` was modified for QIDI Max 4 homing behavior and timing on 2026-07-26. `homing-sync-reset.py` carries the same modifications against the alternate QIDI source variant dated 2026-07-28. Copyright and licensing notices in those files remain in effect.

`installer/system/klipper_chelper/qidi_c_helper.so` is Klipper's C helper library built unmodified from commit `ca815f52c8420225f93f90698768a5c4714654a9`, byte for byte the library QIDI ships. `installer/system/klipper_chelper/c_helper.so` is the same code built with the patches in [`chelper/`](chelper/), backports of later upstream Klipper commits. [`scripts/build_klipper_chelper.py`](scripts/build_klipper_chelper.py) and [`chelper/README.md`](chelper/README.md) hold the steps that rebuild both.

`klipper/` is upstream Klipper v0.14.0 (commit `c9559fa04`) with QIDI's GPL-3.0 Klipper sources for the Max 4 (01.01.07.01), Plus 5 and Smart 5 (01.01.02.01) merged in and modified for Valve Oil from 2026-11-22; [`klipper/README.md`](klipper/README.md) describes the changes. Copyright and licensing notices in its files remain in effect, and `klipper/COPYING` is Klipper's license text.

`resonance_sweep.py` adapts the sweeping resonance test from Klipper's `klippy/extras/resonance_tester.py` (Copyright (C) 2020-2024 Dmitry Butyugin) and the junction calculation from `klippy/toolhead.py` (Copyright (C) 2016-2024 Kevin O'Connor) on 2026-11-07, as credited in its header.

### Fluidd base configuration

- Source: <https://github.com/fluidd-core/fluidd-config>
- License: GPL-3.0-only
- Path: `config/fluidd.cfg`

### QIDI Fluidd box filament source

- Upstream: <https://github.com/fluidd-core/fluidd>
- License: GPL-3.0-only
- Path: `installer/tests/fixtures/fluidd_box_variables.js`

The fixture contains the unmodified minified `getVariables` getter, printer-store mutations, and QIDI Box card picker accessors extracted from the QIDI Max 4 firmware `01.01.07.01` Fluidd `v1.30.5` bundle `/home/qidi/fluidd/assets/index-DlFvHGAh.js`. Fluidd is distributed under GNU GPLv3.

### QIDI Moonraker metadata storage

- Upstream: <https://github.com/Arksine/moonraker>
- License: GPL-3.0-only
- Path: `installer/tests/fixtures/moonraker_metadata_storage.py`

The fixture contains the unmodified `MetadataStorage` class and metascan handler extracted from QIDI Max 4 Moonraker source captured on 2026-09-15. The enclosing module credits Copyright (C) 2020 Eric Callahan and distribution under GNU GPLv3. The fixture retains that attribution without the contact address; imports and service dependencies are supplied by the test harness.

### Moonraker MQTT component

- Upstream: <https://github.com/Arksine/moonraker>
- License: GPL-3.0-only
- Path: `installer/system/vo_moonraker_mqtt.py`

The file is Moonraker `v0.9.3`'s `moonraker/components/mqtt.py`, Copyright (C) 2021 Eric Callahan, distributed under GNU GPLv3, with the contact address left out of its copyright line. Valve Oil changed it on 2026-11-22: a header says where it comes from, and its `load_component` at the end loads a stand-in that connects nowhere when `moonraker.conf` has no `[mqtt]` section.

### Moonraker Timelapse

- Source: <https://github.com/mainsail-crew/moonraker-timelapse>
- License: GPL-3.0-only
- Paths:
  - `config/timelapse.cfg`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/timelapse.cfg`
  - `installer/tests/fixtures/runtime/base/config/timelapse.cfg`

### Klipper Macros

- Source: <https://github.com/jschuh/klipper-macros>
- License: GPL-3.0-only
- Roots:
  - `config/klipper-macros-qd/`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/klipper-macros-qd/`
  - `installer/tests/fixtures/runtime/base/config/klipper-macros-qd/`
- Applicable filenames where present:
  - `bed_mesh.cfg`
  - `beep.cfg`
  - `fans.cfg`
  - `filament.cfg`
  - `globals.cfg`
  - `heaters.cfg`
  - `idle.cfg`
  - `kinematics.cfg`
  - `park.cfg`
  - `pause_resume_cancel.cfg`
  - `start_end.cfg`
  - `state.cfg`
  - `status_events.cfg`
  - `velocity.cfg`

These files contain QIDI and project modifications to upstream macro material. Embedded copyright and licensing notices remain in effect. QIDI-specific files not listed above remain covered by the QIDI stock-material section.

### Klipper Adaptive Meshing and Purging

- Source: <https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging>
- License: GPL-3.0-only
- Paths:
  - `config/KAMP/`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/KAMP/`

### crowsnest configuration

- Source: <https://github.com/mainsail-crew/crowsnest>
- License: GPL-3.0-only
- Paths:
  - `config/crowsnest.conf`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/crowsnest.conf`

## GPL-2.0-only components

The full GPLv2 text is available in [`LICENSES/GPL-2.0-only.txt`](LICENSES/GPL-2.0-only.txt).

### Linux kernel modules

- Source: <https://github.com/armbian/linux-rockchip> commit `709c51c64e1652d4f8c87b1815db86f56d188268`
- License: GPL-2.0-only
- Paths:
  - `installer/system/zram/*.ko`
  - `installer/system/tun/tun.ko`

The `lz4_compress`, `lz4_decompress`, `lz4`, `zsmalloc`, `zram`, and `tun` modules are built unmodified from that source for QIDI's Max 4 kernel. [`scripts/build_kernel_modules.py`](scripts/build_kernel_modules.py) and [`kernel/`](kernel/) hold the configuration and steps that rebuild them.

## Vendored Python dependencies

Vendored dependency versions and artifact hashes are recorded in `installer/runtime/vendor/DEPENDENCIES.yaml`. The corresponding source is under `installer/runtime/vendor/`.

| Component | Version | License | License text |
|---|---:|---|---|
| PyYAML | 6.0.2 | MIT | [`LICENSES/PyYAML-MIT.txt`](LICENSES/PyYAML-MIT.txt) |
| Rich | 13.9.4 | MIT | [`LICENSES/Rich-MIT.txt`](LICENSES/Rich-MIT.txt) |
| typing_extensions | 4.12.2 | PSF-2.0 | [`LICENSES/typing_extensions-PSF-2.0.txt`](LICENSES/typing_extensions-PSF-2.0.txt) |

`installer/runtime/vendor/rich/_spinners.py` includes cli-spinners material under the MIT license.

## Add-on files in release bundles

Release bundles carry unmodified copies of the add-on files pinned under `addons:` in [`installer/package.yaml`](installer/package.yaml). The bundle builder downloads them; they are not part of this repository.

- OctoEverywhere: the release source archive from <https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere>, licensed under the GNU Affero General Public License version 3. The archive includes its `LICENSE`.
- Obico (moonraker-obico): the release source archive from <https://github.com/TheSpaghettiDetective/moonraker-obico>, licensed under the GNU Affero General Public License version 3. The archive includes its `LICENSE` and the prebuilt Janus WebRTC server it ships. On the printer, Valve Oil adds its own launcher, `installer/system/vo_obico.py`, beside the unmodified source.
- Python source archives from PyPI, which the printer builds into packages: each carries its own license.
- Python wheels from PyPI, including pip: each wheel carries its own license in its `.dist-info` directory.

Tailscale is not carried in release bundles. When the operator installs the Tailscale add-on, the printer downloads Tailscale's own unmodified build for arm64 from <https://pkgs.tailscale.com>, pinned by sha256 under `addons:`. Tailscale is licensed under the BSD 3-Clause License; its source is at <https://github.com/tailscale/tailscale>.

ZeroTier is not carried in release bundles either. When the operator installs the ZeroTier add-on, the printer downloads ZeroTier's own unmodified Debian 11 package for arm64 from <https://download.zerotier.com>, pinned by sha256 under `addons:`, and keeps only its `zerotier-one` program. ZeroTier's agent is licensed under the Mozilla Public License 2.0, and the other components in ZeroTier's build under the ZeroTier Source-Available License for Non-Commercial Use; its source and both licences are at <https://github.com/zerotier/ZeroTierOne>.

## Montserrat

- Source: <https://github.com/google/fonts/tree/main/ofl/montserrat> (`Montserrat[wght].ttf` at commit `76fca9fd0bb4ea46583f92e978660f3984ab9442`, font version 9.000), from <https://github.com/JulietaUla/Montserrat>
- Copyright 2024 The Montserrat.Git Project Authors
- License: SIL Open Font License 1.1
- License text: [`LICENSES/Montserrat-OFL-1.1.txt`](LICENSES/Montserrat-OFL-1.1.txt), also beside the font as `scripts/fonts/OFL.txt`
- Paths:
  - `scripts/fonts/Montserrat-VF.ttf` (unmodified, renamed; used to build, not shipped)
  - `installer/system/touchscreen/touch-ui.bin`, `installer/system/touchscreen/installing.png`, and `installer/system/touchscreen/updating.png`, which carry glyphs that [`scripts/render_touchscreen_images.py`](scripts/render_touchscreen_images.py) rasterizes from it

## Valve Oil UI

`ui/` is repository-authored and covered by the repository license, except as follows.

- `ui/src/qidi/` and `ui/src/workers/` port logic from this project's Fluidd fork, <https://github.com/thelegendtubaguy/fluidd> (GPL-3.0-only), as credited in each file's header: the QIDI Box, printer warnings, Valve Oil status and add-ons, bed soak, and the G-code preview parser.
- `ui/src/styles.css` keeps component styles adapted from Beautiful UI, <https://github.com/slev12397/beautiful-ui>, Copyright (c) 2026 Shane Levine, under the MIT License, with Valve Oil's own colors. The full text is in [`LICENSES/Beautiful-UI-MIT.txt`](LICENSES/Beautiful-UI-MIT.txt).
- The build bundles npm packages, chiefly React and React DOM (MIT), lucide-react (ISC), CodeMirror (MIT), and the Montserrat typeface from `@fontsource-variable/montserrat` (SIL Open Font License 1.1). Their versions are pinned in `ui/package-lock.json`, and each build writes their license texts to `licenses.md`, which the installer ships with the build.

## OpenSpec-generated workflow files

- Source: <https://github.com/Fission-AI/OpenSpec>
- License: MIT
- License text: [`LICENSES/OpenSpec-MIT.txt`](LICENSES/OpenSpec-MIT.txt)
- Paths:
  - `.pi/prompts/opsx-*.md`
  - `.pi/skills/openspec-*/SKILL.md`

## Additional project license

`.pi/skills/qidi-firmware-release-update/SKILL.md` is licensed under the MIT License declared in that file. The full text is available in [`LICENSES/QIDI-firmware-release-update-MIT.txt`](LICENSES/QIDI-firmware-release-update-MIT.txt).

## QIDI stock material

The following paths contain QIDI firmware configuration material:

- `config/`, excluding project-authored additions and the separately identified third-party components above.
- `installer/stock/qidi-max4-defaults/firmwares/*/config/`, excluding the separately identified third-party components above.

Snapshot source: <https://github.com/thelegendtubaguy/Qidi-Max4-Defaults>

No standalone license for the remaining QIDI-authored configuration material was found in the source snapshots. The repository license does not replace or override any QIDI rights in that material. Embedded copyright and license notices remain in effect.

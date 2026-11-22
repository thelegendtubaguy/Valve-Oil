from __future__ import annotations

import hashlib
import io
import os
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

from installer.runtime import boot_logo, boot_screen, update_screen
from installer.runtime.reporter import create_reporter
from installer.tests.helpers import temp_path

PAGE = 2048


def _bmp(fill: int) -> bytes:
    """QIDI's boot logo layout: a top-down 24-bit 480x800 BMP."""
    pixels = bytes([fill]) * (480 * 800 * 3)
    header = struct.pack("<2sIIIIiiHHIIiiII", b"BM", 54 + len(pixels), 0, 54, 40, 480, -800, 1, 24, 0, len(pixels),
                         2835, 2835, 0, 0)
    return header + pixels


def _boot_image() -> bytes:
    """A boot image of the reviewed layout: kernel, no ramdisk, and a resource image with a device tree and both
    logos, in a partition with room to spare."""
    kernel = os.urandom(5000)
    entries = [(b"rk-kernel.dtb", os.urandom(700)), (b"logo.bmp", _bmp(0x22)), (b"logo_kernel.bmp", _bmp(0x22))]
    blocks, data, table = 1 + len(entries), b"", b""
    for name, blob in entries:
        start = blocks + len(data) // 512
        table += (b"ENTR" + name.ljust(256, b"\0") + struct.pack("<II", start, len(blob))).ljust(512, b"\0")
        data += blob + b"\0" * (-len(blob) % 512)
    resource = struct.pack("<4sHHBBI", b"RSCE", 0, 0, 1, 1, len(entries)).ljust(512, b"\0") + table + data

    def padded(blob):
        return blob + b"\0" * (-len(blob) % PAGE)

    header = bytearray(struct.pack("<8s10I", b"ANDROID!", len(kernel), 0x10008000, 0, 0x11000000, len(resource),
                                   0x10F00000, 0x10000100, PAGE, 0, 0).ljust(PAGE, b"\0"))
    digest = hashlib.sha1()
    for blob in (kernel, b"", resource):
        digest.update(blob)
        digest.update(struct.pack("<I", len(blob)))
    header[576:596] = digest.digest()
    image = bytes(header) + padded(kernel) + padded(resource)
    return image + b"\0" * (len(image) // 4)


class BootLogoTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_path("boot-logo")
        self.environ = {boot_logo.SYSTEM_ROOT_ENV: str(self.root / "system")}
        self.device = self.root / "system" / boot_logo.DEVICE.lstrip("/")
        self.device.parent.mkdir(parents=True)
        self.original = _boot_image()
        self.device.write_bytes(self.original)
        self.paths = SimpleNamespace(printer_data_root=self.root / "printer_data")
        self.paths.printer_data_root.mkdir()
        self.output = io.StringIO()
        self.reporter = create_reporter(stream=self.output, prefer_plain=True)

    def step(self, action):
        action(self.paths, reporter=self.reporter, environ=self.environ)
        return self.device.read_bytes()

    def test_the_logo_is_replaced_in_place_and_uninstall_puts_qidis_back(self):
        written = self.step(boot_logo.install)
        self.assertNotEqual(written, self.original)
        layout = boot_logo.layout(written)  # still a boot image of the reviewed layout, with a matching id
        # Only the two logos and the header's block changed; the kernel, the device tree, and the rest did not.
        changed = [block for block in range(len(written) // 512)
                   if written[block * 512:(block + 1) * 512] != self.original[block * 512:(block + 1) * 512]]
        logo_blocks = {block for start, size in layout.logos for block in range(start // 512, -(-(start + size) // 512))}
        self.assertTrue(set(changed) - {1} <= logo_blocks, changed)
        start, size = layout.logos[0]
        self.assertEqual(written[start:start + size], written[layout.logos[1][0]:layout.logos[1][0] + size])
        # The upright logo, turned into the portrait BMP as the framebuffer turns the screen.
        logo = update_screen.decode_png(boot_screen.BOOT_LOGO_IMAGE.read_bytes())
        bmp = written[start + 54:start + size]
        for ux, uy in ((0, 0), (400, 240), (150, 300), (799, 479)):
            stored = ((799 - ux) * 480 + uy) * 3
            self.assertEqual(bmp[stored:stored + 3], logo[uy][ux * 3:ux * 3 + 3][::-1])
        self.assertEqual(self.step(boot_logo.install), written)  # nothing more to write
        self.assertEqual(self.step(boot_logo.uninstall), self.original)
        self.assertFalse(boot_logo.record_path(self.paths).exists())
        self.assertFalse(any((self.paths.printer_data_root / boot_logo.BACKUP_DIR).iterdir()))

    def test_a_boot_partition_that_isnt_the_reviewed_layout_is_left_alone(self):
        corrupt_id = bytearray(self.original)
        corrupt_id[576] ^= 0xFF
        no_logo = self.original.replace(b"logo_kernel.bmp", b"other_file.bmp")
        for name, image in (("id", bytes(corrupt_id)), ("logos", no_logo), ("not a boot image", b"\0" * 4096)):
            with self.subTest(name):
                self.device.write_bytes(image)
                self.assertEqual(self.step(boot_logo.install), image)
                self.assertIn("Kept QIDI's boot logo", self.output.getvalue())
                self.assertFalse(boot_logo.record_path(self.paths).exists())

    def test_uninstall_leaves_a_partition_changed_since_valve_oil_wrote_it(self):
        self.step(boot_logo.install)
        replaced = _boot_image()
        self.device.write_bytes(replaced)
        self.assertEqual(self.step(boot_logo.uninstall), replaced)
        self.assertIn("left as it is", self.output.getvalue())


class BootAnimationTests(unittest.TestCase):
    def setUp(self):
        self.globals = {}
        exec(compile(boot_screen.player_source(), "vo_boot_animation.py", "exec"), self.globals)
        self.player = SimpleNamespace(**self.globals)
        self.animation = self.player.decode(__import__("base64").b64decode(self.player.ANIMATION))

    def framebuffer(self, name, *, sliced=True):
        """A portrait 32-bit framebuffer in a file, as on the printers, drawn by slicing or pixel by pixel."""
        path = temp_path("boot-animation") / name
        path.write_bytes(bytes(480 * 800 * 4))
        screen = self.player.Screen.__new__(self.player.Screen)
        screen.fd = os.open(path, os.O_RDWR)
        screen.width, screen.height, screen.pixel_bytes = 480, 800, 4
        screen.channels = ((16, 8), (8, 8), (0, 8))
        screen.stride, screen.start, screen.turned, screen.upright, screen.pixels = 480 * 4, 0, True, (800, 480), {}
        screen.bytes_at = (2, 1, 0) if sliced else None
        self.addCleanup(os.close, screen.fd)
        return screen, path

    def test_the_animation_plays_and_ends_on_the_boot_logo(self):
        _, width, height, frames = self.animation
        self.assertEqual((width, height), update_screen.IMAGE_SIZE)
        logo = update_screen.decode_png(boot_screen.BOOT_LOGO_IMAGE.read_bytes())
        self.assertEqual(frames[0][0][4], b"".join(logo), "the animation starts on the boot logo")
        played, played_path = self.framebuffer("played")
        drawn = []
        original = played.draw
        played.draw = lambda *args: drawn.append(args[:4]) or original(*args)
        ticks = iter(range(len(frames) * 2 + 7))
        self.player.play(self.animation, played, stop=lambda: next(ticks) >= len(frames) * 2 + 5,
                         clock=lambda: 0.0, sleep=lambda seconds: None)
        self.assertGreater(len(drawn), len(frames) * 2)  # it looped
        still, still_path = self.framebuffer("still", sliced=False)
        still.draw(0, 0, width, height, frames[0][0][4])
        self.assertEqual(played_path.read_bytes(), still_path.read_bytes())

        # QIDI's app first shows its logo alone on a plain background, drawn in two bands some time apart, and may draw
        # while a frame is being drawn: the animation draws over the whole logo screen and plays on. When the app then
        # draws its home screen, the animation stops and leaves the app's screen as the app drew it.
        app, app_path = self.framebuffer("app")
        size = 480 * 800 * 4
        splash = bytearray(b"\x14\x14\x14\x00" * 480 * 800)
        for line in range(200, 600):  # the logo, in the middle of the upright screen turned onto this framebuffer
            splash[line * 480 * 4 + 160 * 4:line * 480 * 4 + 320 * 4] = b"\xff\x80\x40\x00" * 160
        home = b"\x11" * size
        now, writes, app_drew = [0.0], [0], []
        write = app._write

        def player_writes(data, offset):
            writes[0] += 1
            if writes[0] == 2000:  # in the middle of a frame
                os.pwrite(app.fd, bytes(splash[size // 2:]), size // 2)
                app_drew.append("half of the logo screen")
            elif writes[0] == 6000:
                os.pwrite(app.fd, home, 0)
                app_drew.append("the home screen")
            write(data, offset)

        def sleep(seconds):
            if app_drew == ["half of the logo screen"]:
                os.pwrite(app.fd, bytes(splash[:size // 2]), 0)
                app_drew.append("the logo screen")
            now[0] += seconds

        app._write = player_writes
        self.assertTrue(self.player.play(self.animation, app, stop=lambda: False, clock=lambda: now[0], sleep=sleep))
        self.assertEqual(app_drew, ["half of the logo screen", "the logo screen", "the home screen"])
        self.assertTrue(app_path.read_bytes() == home, "the home screen is left as the app drew it")

    def test_nothing_is_drawn_while_a_firmware_update_or_an_install_holds_the_screen(self):
        root = temp_path("boot-animation-owned")
        drew = []
        self.globals.update(PRINTER_DATA=str(root), app_running=lambda: False, Screen=lambda: drew.append(True))
        for owner in self.player.SCREEN_OWNERS:
            with self.subTest(owner=owner):
                (root / owner).write_text("{}")
                self.assertEqual(self.player.main(), 0)
                (root / owner).unlink()
        self.assertEqual(drew, [])

    def test_the_player_waits_for_the_framebuffer_to_become_its_own(self):
        now, opened = [0.0], []

        def screen():
            if now[0] < 2:
                raise PermissionError("[Errno 13] Permission denied: '/dev/fb0'")
            opened.append(now[0])
            return "screen"

        def sleep(seconds):
            now[0] += seconds

        self.globals.update(Screen=screen)
        self.assertEqual(self.player.open_screen(sleep=sleep, clock=lambda: now[0]), "screen")
        self.assertEqual(len(opened), 1)
        now[0] = 0.0
        self.globals.update(FRAMEBUFFER_WAIT_SECONDS=1)
        with self.assertRaises(PermissionError):
            self.player.open_screen(sleep=sleep, clock=lambda: now[0])


if __name__ == "__main__":
    unittest.main()

# vo:boot-animation
"""Valve Oil's boot animation, played on the touchscreen from early in the boot until QIDI's touchscreen app runs.

Installed by Valve Oil as /usr/local/lib/vo_boot_animation.py, with the animation (installer/runtime/boot_screen.py
describes its format) in ANIMATION, and run by vo-boot-animation.service. It draws the first frame whole, then only
what changes, until QIDI's app draws its first screen: the app draws into the same framebuffer, so the player keeps a
copy of what it drew and looks, before each frame, whether the framebuffer holds something else. The app first shows
its logo alone on a plain background for a few seconds: the player waits for the app to finish drawing it, draws its own
screen over it once and plays on, and stops once the app draws anything else, as its home screen. After 2 minutes, or when a QIDI firmware
update or an install takes the screen, it puts the changing areas back to the first frame and ends, so the screen
holds the Valve Oil logo. It draws nothing while a firmware update or an install holds the screen, and nothing when
the app already runs.

Rockchip's kernel keeps showing the bootloader's logo until a program sets the display's mode, so what is written to the
framebuffer stays unseen until then: after drawing the first frame, the player sets the framebuffer's mode as it is,
which puts the framebuffer on the screen, as QIDI's app does when it starts. What it did and why it ended goes to LOG,
since the system journal drops what is logged before the clock is set.
"""
import base64
import fcntl
import os
import struct
import sys
import time
import zlib

ANIMATION = None

FRAMEBUFFER = "/dev/fb0"
PRINTER_DATA = "/home/qidi/printer_data"
# A firmware update resuming at boot, or an install, shows its own screen.
SCREEN_OWNERS = (".vo_firmware_update_pending.json", ".vo_install_screen")
APP = b"qidiclient"
LONGEST_SECONDS = 120
# udev gives the framebuffer to the video group partway through boot, and until then only root can open it. On the
# Smart 5 the player got there first on two boots of three.
FRAMEBUFFER_WAIT_SECONDS = 15
LOG = PRINTER_DATA + "/logs/vo_boot_animation.log"
FBIOGET_VSCREENINFO = 0x4600
FBIOPUT_VSCREENINFO = 0x4601
FBIOGET_FSCREENINFO = 0x4602
FB_ACTIVATE_FORCE = 128
FIX_SCREENINFO = struct.Struct("@16sLIIIIHHHI")


def decode(data):
    """The animation's (fps, width, height, frames), each frame a list of (x, y, width, height, rgb)."""
    magic, version, fps, width, height, count = struct.unpack_from("<4sBBHHH", data, 0)
    if magic != b"VOBA" or version != 1:
        raise ValueError("not a Valve Oil boot animation")
    position, frames = 12, []
    for _ in range(count):
        (number,) = struct.unpack_from("<B", data, position)
        position += 1
        frame = []
        for _ in range(number):
            x, y, w, h, size = struct.unpack_from("<HHHHI", data, position)
            position += 12
            frame.append((x, y, w, h, zlib.decompress(data[position:position + size])))
            position += size
        frames.append(frame)
    return fps, width, height, frames


class DrawnOver(Exception):
    """The app drew where the player was about to."""


class Screen:
    """The framebuffer, drawn in the coordinates of the upright screen. A portrait framebuffer shows the upright
    screen turned 90 degrees counterclockwise, as QIDI's app draws it."""

    mirror = None  # what the framebuffer holds from what the player drew, once watched

    def __init__(self, device=FRAMEBUFFER):
        self.fd = os.open(device, os.O_RDWR | os.O_CLOEXEC)
        info = struct.unpack_from("17I", fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, bytes(160)))
        self.width, self.height, xoffset, yoffset, bits = info[0], info[1], info[4], info[5], info[6]
        self.channels = ((info[8], info[9]), (info[11], info[12]), (info[14], info[15]))
        if bits not in (16, 24, 32) or any(not 0 < length <= 8 for _, length in self.channels):
            raise ValueError("unsupported framebuffer format")
        self.pixel_bytes = bits // 8
        try:
            stride = FIX_SCREENINFO.unpack_from(fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO,
                                                            bytes(FIX_SCREENINFO.size)))[9]
        except OSError:
            stride = 0
        self.stride = stride or info[2] * self.pixel_bytes
        self.start = yoffset * self.stride + xoffset * self.pixel_bytes
        self.turned = self.height > self.width
        self.upright = (self.height, self.width) if self.turned else (self.width, self.height)
        self.pixels = {}
        # Byte positions of R, G, and B in a pixel, when each is a whole byte: then pixels are converted by slicing.
        self.bytes_at = None
        if self.pixel_bytes in (3, 4) and all(length == 8 and offset % 8 == 0 for offset, length in self.channels):
            self.bytes_at = tuple(offset // 8 for offset, _ in self.channels)

    def close(self):
        os.close(self.fd)

    def _write(self, data, offset):
        if self.mirror is not None:
            # A line the app drew since is left to the app: drawing over it would leave part of a frame on its screen.
            at = offset - self.start
            if os.pread(self.fd, len(data), offset) != self.mirror[at:at + len(data)]:
                raise DrawnOver()
            self.mirror[at:at + len(data)] = data
        os.pwrite(self.fd, data, offset)

    def watch(self):
        """Keep a copy of the framebuffer, which the player's draws update, for drawn_over."""
        self.mirror = bytearray(os.pread(self.fd, self.stride * self.height, self.start))

    def drawn_over(self):
        """Something else, QIDI's app, drew into the framebuffer since watch."""
        return self.mirror is not None and self.read() != self.mirror

    def read(self):
        return os.pread(self.fd, self.stride * self.height, self.start)

    def becomes_splash(self, *, sleep=time.sleep, clock=time.monotonic):
        """What is being drawn into the framebuffer turns out to be the app's logo screen. The app draws a screen in
        bands, the bottom one first, 70 ms apart on an idle Max 4 and further apart while the boot keeps the CPU busy:
        it is not the logo screen once it stays the same for half a second without being one, or after 2 seconds."""
        data = self.read()
        start = changed = clock()
        while not self.is_splash(data):
            if clock() - changed >= 0.5 or clock() - start >= 2.0:
                return False
            sleep(0.02)
            again = self.read()
            if again != data:
                data, changed = again, clock()
        return True

    def is_splash(self, data):
        """The framebuffer shows one color outside a box in the middle of the upright screen, which holds a logo."""
        box_w, box_h = 400, 200
        upright_w, upright_h = self.upright
        x0, y0 = (upright_w - box_w) // 2, (upright_h - box_h) // 2
        if self.turned:  # upright (x, y) is framebuffer (y, height - 1 - x)
            rows, columns = (self.height - x0 - box_w, self.height - x0), (y0, y0 + box_h)
        else:
            rows, columns = (y0, y0 + box_h), (x0, x0 + box_w)
        size = self.pixel_bytes
        pixel = bytes(data[:size])
        for row in range(self.height):
            line = data[row * self.stride:row * self.stride + self.width * size]
            if rows[0] <= row < rows[1]:
                parts = (line[:columns[0] * size], line[columns[1] * size:])
            else:
                parts = (line,)
            if any(part != pixel * (len(part) // size) for part in parts):
                return False
        return True

    def show(self):
        """Set the framebuffer's mode as it is, with FB_ACTIVATE_FORCE, so the display shows the framebuffer."""
        info = bytearray(fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, bytes(160)))
        struct.pack_into("I", info, 21 * 4, FB_ACTIVATE_FORCE)  # activate: now, even with the mode unchanged
        fcntl.ioctl(self.fd, FBIOPUT_VSCREENINFO, bytes(info))

    def _pixel(self, rgb):
        pixel = self.pixels.get(rgb)
        if pixel is None:
            value = 0
            for component, (offset, length) in zip(rgb, self.channels):
                value |= (component >> (8 - length)) << offset
            pixel = self.pixels[rgb] = value.to_bytes(self.pixel_bytes, "little")
        return pixel

    def _convert(self, rgb):
        return [self._pixel(rgb[i:i + 3]) for i in range(0, len(rgb), 3)]

    def _sliced(self, red, green, blue, count):
        out = bytearray(count * self.pixel_bytes)
        for position, channel in zip(self.bytes_at, (red, green, blue)):
            out[position::self.pixel_bytes] = channel
        return out

    def draw(self, x, y, w, h, rgb):
        if self.bytes_at is not None:
            if not self.turned:
                for row in range(h):
                    line = rgb[row * w * 3:(row + 1) * w * 3]
                    self._write(self._sliced(line[0::3], line[1::3], line[2::3], w),
                              self.start + (y + row) * self.stride + x * self.pixel_bytes)
                return
            step = w * 3
            for column in range(w):
                self._write(self._sliced(rgb[column * 3::step], rgb[column * 3 + 1::step],
                                                rgb[column * 3 + 2::step], h),
                          self.start + (self.height - 1 - (x + column)) * self.stride + y * self.pixel_bytes)
            return
        pixels = self._convert(rgb)
        if not self.turned:
            for row in range(h):
                self._write(b"".join(pixels[row * w:(row + 1) * w]),
                          self.start + (y + row) * self.stride + x * self.pixel_bytes)
            return
        # Upright (x, y) shows at framebuffer (y, height - 1 - x): each upright column is a framebuffer row.
        for column in range(w):
            data = b"".join(pixels[row * w + column] for row in range(h))
            self._write(data, self.start + (self.height - 1 - (x + column)) * self.stride + y * self.pixel_bytes)


def app_running():
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/comm", "rb") as handle:
                    if handle.read().strip() == APP:
                        return True
            except OSError:
                pass
    return False


def screen_owned():
    return any(os.path.exists(os.path.join(PRINTER_DATA, name)) for name in SCREEN_OWNERS)


def crop(rgb, width, x, y, w, h):
    return b"".join(rgb[((y + row) * width + x) * 3:((y + row) * width + x + w) * 3] for row in range(h))


def play(animation, screen, *, stop, clock=time.monotonic, sleep=time.sleep):
    """Play until stop() or until something else draws on the screen; whether something else did."""
    fps, width, height, frames = animation
    if (width, height) != screen.upright:
        raise ValueError(f"the animation is {width}x{height}, the screen {screen.upright[0]}x{screen.upright[1]}")
    first = frames[0][0][4]
    screen.draw(0, 0, width, height, first)
    try:
        screen.show()
        log("the framebuffer is on the screen")
    except OSError as exc:
        log(f"the framebuffer's mode could not be set: {exc}")
    screen.watch()
    # The area later frames change, put back to the first frame when the animation ends.
    changed = [rect for frame in frames[1:] for rect in frame]
    if changed:
        left, top = min(r[0] for r in changed), min(r[1] for r in changed)
        right, bottom = max(r[0] + r[2] for r in changed), max(r[1] + r[3] for r in changed)
    period, next_at, index, drawn_over, covered = 1.0 / fps, clock(), 1, False, False
    try:
        while changed and not stop():
            if frames[index] and screen.drawn_over():
                if not covered and screen.becomes_splash(sleep=sleep, clock=clock):
                    # The app's logo screen, which it doesn't draw again until it shows its home screen.
                    covered = True
                    log("drew over the touchscreen app's logo screen")
                    index, next_at = 1, clock()
                    try:
                        screen.watch()
                        screen.draw(0, 0, width, height, first)
                    except DrawnOver:
                        pass  # the app drew on: the check above ends the animation
                    continue
                drawn_over = True
                break
            try:
                for x, y, w, h, rgb in frames[index]:
                    screen.draw(x, y, w, h, rgb)
            except DrawnOver:
                continue
            index = index + 1 if index + 1 < len(frames) else 0
            if index == 0:
                # Frame 0 is the whole screen: the loop's last frame leads back to it through frame 1's changes.
                index = 1
            next_at += period
            sleep(max(0.0, next_at - clock()))
    finally:
        if changed and not drawn_over and not screen_owned():
            try:
                screen.draw(left, top, right - left, bottom - top,
                            crop(first, width, left, top, right - left, bottom - top))
            except DrawnOver:
                pass
    return drawn_over


_log = None


def log(message):
    try:
        _log.write(f"{time.monotonic():.2f} {message}\n")
        _log.flush()
    except (AttributeError, OSError, ValueError):
        pass


def open_screen(*, sleep=time.sleep, clock=time.monotonic):
    """The framebuffer, once the player may open it."""
    deadline = clock() + FRAMEBUFFER_WAIT_SECONDS
    while True:
        try:
            return Screen()
        except PermissionError:
            if clock() >= deadline:
                raise
            sleep(0.1)


def main():
    global _log
    try:
        _log = open(LOG, "w", encoding="utf-8")
    except OSError:
        pass
    reason = ("no animation" if ANIMATION is None else "a firmware update or an install holds the screen"
              if screen_owned() else "the touchscreen app runs" if app_running() else None)
    if reason is not None:
        log("not played: " + reason)
        return 0
    animation = decode(base64.b64decode(ANIMATION))
    started = time.monotonic()
    screen = open_screen()
    log(f"playing on a {screen.width}x{screen.height} framebuffer")
    why, app = [], []

    def stop():
        if not app and app_running():
            app.append(True)
            log("the touchscreen app started")
        reason = ("a firmware update or an install took the screen" if screen_owned() else
                  "the time limit" if time.monotonic() - started > LONGEST_SECONDS else None)
        if reason is not None:
            why.append(reason)
        return reason is not None

    try:
        drawn_over = play(animation, screen, stop=stop)
    finally:
        screen.close()
    log("ended: " + ("the touchscreen app drew" if drawn_over else why[0] if why else "the animation has one frame"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - a boot animation never stops the boot
        log(f"failed: {type(exc).__name__}: {exc}")
        sys.stderr.write(f"Valve Oil boot animation: {type(exc).__name__}: {exc}\n")
        sys.exit(0)

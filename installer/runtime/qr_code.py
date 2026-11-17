"""A QR code encoder for the touchscreen (touch_ui.py), with no third-party module.

It encodes bytes in byte mode at error correction level L or M, in the smallest version from 1 to 40 that holds them,
and picks the mask with the lowest penalty, as ISO/IEC 18004 describes. That covers a prefilled GitHub issue link of
a few hundred characters. encode() returns the modules as rows of booleans, True for dark, without the quiet zone.
"""
from __future__ import annotations

import itertools

# Per version (index 1 to 40): error correction codewords per block, and the number of blocks.
_ECC_PER_BLOCK = {
    "L": (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28,
          30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28,
          28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
}
_BLOCKS = {
    "L": (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17,
          18, 19, 19, 20, 21, 22, 24, 25),
    "M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29,
          31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
}
_FORMAT_LEVEL = {"L": 1, "M": 0}
_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


class DataTooLong(ValueError):
    pass


def encode(data: bytes | str, level: str = "M") -> list[list[bool]]:
    """The QR code of `data` (UTF-8 when text) at error correction `level`; DataTooLong when no version holds it."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if level not in _BLOCKS:
        raise ValueError(f"unsupported error correction level {level!r}")
    for version in range(1, 41):
        capacity = _data_codewords(version, level) * 8
        if 4 + (8 if version <= 9 else 16) + 8 * len(data) <= capacity:
            break
    else:
        raise DataTooLong(f"{len(data)} bytes don't fit in a version 40 QR code at level {level}")
    codewords = _with_error_correction(_data_bits(data, version, capacity), version, level)
    return _Matrix(version).draw(codewords, level)


def _raw_modules(version: int) -> int:
    """The modules left for data and error correction once the function patterns are placed."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        aligns = version // 7 + 2
        result -= (25 * aligns - 10) * aligns - 55
        if version >= 7:
            result -= 36
    return result


def _data_codewords(version: int, level: str) -> int:
    return _raw_modules(version) // 8 - _ECC_PER_BLOCK[level][version] * _BLOCKS[level][version]


def _data_bits(data: bytes, version: int, capacity: int) -> bytes:
    bits = [(0b0100, 4), (len(data), 8 if version <= 9 else 16)] + [(byte, 8) for byte in data]
    value, length = 0, 0
    for field, width in bits:
        value, length = (value << width) | field, length + width
    terminator = min(4, capacity - length)
    value, length = value << terminator, length + terminator
    value, length = value << (-length % 8), length + (-length % 8)
    out = value.to_bytes(length // 8, "big")
    return out + bytes(itertools.islice(itertools.cycle((0xEC, 0x11)), capacity // 8 - len(out)))


def _with_error_correction(data: bytes, version: int, level: str) -> bytes:
    blocks_count, ecc_length = _BLOCKS[level][version], _ECC_PER_BLOCK[level][version]
    total = _raw_modules(version) // 8
    short_blocks = blocks_count - total % blocks_count
    short_length = total // blocks_count - ecc_length  # data codewords in a short block
    divisor = _rs_divisor(ecc_length)
    blocks, position = [], 0
    for index in range(blocks_count):
        length = short_length + (0 if index < short_blocks else 1)
        block = data[position:position + length]
        position += length
        blocks.append((block, _rs_remainder(block, divisor)))
    out = bytearray()
    for column in range(short_length + 1):
        out.extend(block[column] for block, _ in blocks if column < len(block))
    for column in range(ecc_length):
        out.extend(ecc[column] for _, ecc in blocks)
    return bytes(out)


def _gf_multiply(x: int, y: int) -> int:
    product = 0
    for bit in range(7, -1, -1):
        product = (product << 1) ^ ((product >> 7) * 0x11D)
        product ^= ((y >> bit) & 1) * x
    return product


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for index in range(degree):
            result[index] = _gf_multiply(result[index], root)
            if index + 1 < degree:
                result[index] ^= result[index + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data: bytes, divisor: list[int]) -> bytes:
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for index, coefficient in enumerate(divisor):
            result[index] ^= _gf_multiply(coefficient, factor)
    return bytes(result)


class _Matrix:
    def __init__(self, version: int):
        self.version = version
        self.size = size = version * 4 + 17
        self.modules = [[False] * size for _ in range(size)]
        self.function = [[False] * size for _ in range(size)]
        self._function_patterns()

    def set(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self.function[y][x] = True

    def _function_patterns(self) -> None:
        size = self.size
        for index in range(size):
            self.set(6, index, index % 2 == 0)
            self.set(index, 6, index % 2 == 0)
        for x, y in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    if 0 <= x + dx < size and 0 <= y + dy < size:
                        self.set(x + dx, y + dy, max(abs(dx), abs(dy)) not in (2, 4))
        positions = self._alignment_positions()
        last = len(positions) - 1
        for i, x in enumerate(positions):
            for j, y in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)
        self._format_bits(0, "M")  # reserves the format areas; drawn for real per mask
        if self.version >= 7:
            remainder = self.version
            for _ in range(12):
                remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
            bits = self.version << 12 | remainder
            for index in range(18):
                dark = bool((bits >> index) & 1)
                a, b = size - 11 + index % 3, index // 3
                self.set(a, b, dark)
                self.set(b, a, dark)

    def _alignment_positions(self) -> list[int]:
        if self.version == 1:
            return []
        count = self.version // 7 + 2
        step = (self.version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
        return [6] + sorted(self.size - 7 - index * step for index in range(count - 1))

    def _format_bits(self, mask: int, level: str) -> None:
        data = _FORMAT_LEVEL[level] << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412
        bit = [bool((bits >> index) & 1) for index in range(15)]
        size = self.size
        for index in range(6):
            self.set(8, index, bit[index])
        self.set(8, 7, bit[6])
        self.set(8, 8, bit[7])
        self.set(7, 8, bit[8])
        for index in range(9, 15):
            self.set(14 - index, 8, bit[index])
        for index in range(8):
            self.set(size - 1 - index, 8, bit[index])
        for index in range(8, 15):
            self.set(8, size - 15 + index, bit[index])
        self.set(8, size - 8, True)

    def _place(self, codewords: bytes) -> None:
        size, total, index = self.size, len(codewords) * 8, 0
        for right in range(size - 1, 0, -2):
            if right <= 6:
                right -= 1
            upward = ((right + 1) & 2) == 0
            for vertical in range(size):
                y = size - 1 - vertical if upward else vertical
                for x in (right, right - 1):
                    if not self.function[y][x] and index < total:
                        self.modules[y][x] = bool((codewords[index >> 3] >> (7 - (index & 7))) & 1)
                        index += 1

    def _masked(self, mask: int) -> list[list[bool]]:
        test = _MASKS[mask]
        return [[dark != (not function and test(x, y)) for x, (dark, function) in enumerate(zip(row, functions))]
                for y, (row, functions) in enumerate(zip(self.modules, self.function))]

    def draw(self, codewords: bytes, level: str) -> list[list[bool]]:
        self._place(codewords)
        best = None
        for mask in range(8):
            self._format_bits(mask, level)
            candidate = self._masked(mask)
            penalty = _penalty(candidate)
            if best is None or penalty < best[0]:
                best = (penalty, candidate)
        return best[1]


def _penalty(modules: list[list[bool]]) -> int:
    size = len(modules)
    lines = ["".join("1" if dark else "0" for dark in row) for row in modules]
    lines += ["".join(line[x] for line in lines[:size]) for x in range(size)]
    result = 0
    for line in lines:
        for _, run in itertools.groupby(line):
            length = sum(1 for _ in run)
            if length >= 5:
                result += length - 2
        padded = "0000" + line + "0000"
        result += 40 * (_count(padded, "00001011101") + _count(padded, "10111010000"))
    for y in range(size - 1):
        top, bottom = modules[y], modules[y + 1]
        for x in range(size - 1):
            if top[x] == top[x + 1] == bottom[x] == bottom[x + 1]:
                result += 3
    dark = sum(row.count(True) for row in modules)
    total = size * size
    result += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
    return result


def _count(text: str, pattern: str) -> int:
    count, start = 0, text.find(pattern)
    while start >= 0:
        count += 1
        start = text.find(pattern, start + 1)
    return count

# 
# QR Code generator library (Python 2, 3)
# 
# Copyright (c) Project Nayuki. (MIT License)
# https://www.nayuki.io/page/qr-code-generator-library
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
# - The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
# - The Software is provided "as is", without warranty of any kind, express or
#   implied, including but not limited to the warranties of merchantability,
#   fitness for a particular purpose and noninfringement. In no event shall the
#   authors or copyright holders be liable for any claim, damages or other
#   liability, whether in an action of contract, tort or otherwise, arising from,
#   out of or in connection with the Software or the use or other dealings in the
#   Software.
# 
try:
       import micropython  # Use to detect if we're running in a MicroPython environment
       import collections.deque as collections
       import ure as re
except:  # Prefer to catch ModuleNotFoundError but MicroPython instead throws ImportError
       import collections, re


import itertools, sys

"""
This module "qrcodegen", public members:
- Class QrCode:
  - Function encode_text(str text, QrCode.Ecc ecl) -> QrCode
  - Function encode_binary(bytes data, QrCode.Ecc ecl) -> QrCode
  - Function encode_segments(list<QrSegment> segs, QrCode.Ecc ecl,
        int minversion=1, int maxversion=40, mask=-1, boostecl=true) -> QrCode
  - Constants int MIN_VERSION, MAX_VERSION
  - Constructor QrCode(int version, QrCode.Ecc ecl, bytes datacodewords, int mask)
  - Method get_version() -> int
  - Method get_size() -> int
  - Method get_error_correction_level() -> QrCode.Ecc
  - Method get_mask() -> int
  - Method get_module(int x, int y) -> bool
  - Method to_svg_str(int border) -> str
  - Enum Ecc:
    - Constants LOW, MEDIUM, QUARTILE, HIGH
    - Field int ordinal
- Class QrSegment:
  - Function make_bytes(bytes data) -> QrSegment
  - Function make_numeric(str digits) -> QrSegment
  - Function make_alphanumeric(str text) -> QrSegment
  - Function make_segments(str text) -> list<QrSegment>
  - Function make_eci(int assignval) -> QrSegment
  - Constructor QrSegment(QrSegment.Mode mode, int numch, list<int> bitdata)
  - Method get_mode() -> QrSegment.Mode
  - Method get_num_chars() -> int
  - Method get_data() -> list<int>
  - Constants regex NUMERIC_REGEX, ALPHANUMERIC_REGEX
  - Enum Mode:
    - Constants NUMERIC, ALPHANUMERIC, BYTE, KANJI, ECI
"""


# ---- QR Code symbol class ----

class QrCode(object):
	"""A QR Code symbol, which is a type of two-dimension barcode.
	Invented by Denso Wave and described in the ISO/IEC 18004 standard.
	Instances of this class represent an immutable square grid of black and white cells.
	The class provides static factory functions to create a QR Code from text or binary data.
	The class covers the QR Code Model 2 specification, supporting all versions (sizes)
	from 1 to 40, all 4 error correction levels, and 4 character encoding modes.
	
	Ways to create a QR Code object:
	- High level: Take the payload data and call QrCode.encode_text() or QrCode.encode_binary().
	- Mid level: Custom-make the list of segments and call QrCode.encode_segments().
	- Low level: Custom-make the array of data codeword bytes (including
	  segment headers and final padding, excluding error correction codewords),
	  supply the appropriate version number, and call the QrCode() constructor.
	(Note that all ways require supplying the desired error correction level.)"""
	
	# ---- Static factory functions (high level) ----
	
	@staticmethod
	def encode_text(text, ecl):
		"""Returns a QR Code representing the given Unicode text string at the given error correction level.
		As a conservative upper bound, this function is guaranteed to succeed for strings that have 738 or fewer
		Unicode code points (not UTF-16 code units) if the low error correction level is used. The smallest possible
		QR Code version is automatically chosen for the output. The ECC level of the result may be higher than the
		ecl argument if it can be done without increasing the version."""
		segs = QrSegment.make_segments(text)
		return QrCode.encode_segments(segs, ecl)
	
	
	@staticmethod
	def encode_binary(data, ecl):
		"""Returns a QR Code representing the given binary data at the given error correction level.
		This function always encodes using the binary segment mode, not any text mode. The maximum number of
		bytes allowed is 2953. The smallest possible QR Code version is automatically chosen for the output.
		The ECC level of the result may be higher than the ecl argument if it can be done without increasing the version."""
		if not isinstance(data, (bytes, bytearray)):
			raise TypeError("Byte string/list expected")
		return QrCode.encode_segments([QrSegment.make_bytes(data)], ecl)
	
	
	# ---- Static factory functions (mid level) ----
	
	@staticmethod
	def encode_segments(segs, ecl, minversion=1, maxversion=40, mask=-1, boostecl=True):
		"""Returns a QR Code representing the given segments with the given encoding parameters.
		The smallest possible QR Code version within the given range is automatically
		chosen for the output. Iff boostecl is true, then the ECC level of the result
		may be higher than the ecl argument if it can be done without increasing the
		version. The mask number is either between 0 to 7 (inclusive) to force that
		mask, or -1 to automatically choose an appropriate mask (which may be slow).
		This function allows the user to create a custom sequence of segments that switches
		between modes (such as alphanumeric and byte) to encode text in less space.
		This is a mid-level API; the high-level API is encode_text() and encode_binary()."""
		
		if not (QrCode.MIN_VERSION <= minversion <= maxversion <= QrCode.MAX_VERSION) or not (-1 <= mask <= 7):
			raise ValueError("Invalid value")
		
		# Find the minimal version number to use
		for version in range(minversion, maxversion + 1):
			datacapacitybits = QrCode._get_num_data_codewords(version, ecl) * 8  # Number of data bits available
			datausedbits = QrSegment.get_total_bits(segs, version)
			if datausedbits is not None and datausedbits <= datacapacitybits:
				break  # This version number is found to be suitable
			if version >= maxversion:  # All versions in the range could not fit the given data
				msg = "Segment too long"
				if datausedbits is not None:
					msg = "Data length = {} bits, Max capacity = {} bits".format(datausedbits, datacapacitybits)
				raise DataTooLongError(msg)
		if datausedbits is None:
			raise AssertionError()
		
		# Increase the error correction level while the data still fits in the current version number
		for newecl in (QrCode.Ecc.MEDIUM, QrCode.Ecc.QUARTILE, QrCode.Ecc.HIGH):  # From low to high
			if boostecl and datausedbits <= QrCode._get_num_data_codewords(version, newecl) * 8:
				ecl = newecl
		
		# Concatenate all segments to create the data bit string
		bb = _BitBuffer()
		for seg in segs:
			bb.append_bits(seg.get_mode().get_mode_bits(), 4)
			bb.append_bits(seg.get_num_chars(), seg.get_mode().num_char_count_bits(version))
			bb.extend(seg._bitdata)
		assert len(bb) == datausedbits
		
		# Add terminator and pad up to a byte if applicable
		datacapacitybits = QrCode._get_num_data_codewords(version, ecl) * 8
		assert len(bb) <= datacapacitybits
		bb.append_bits(0, min(4, datacapacitybits - len(bb)))
		bb.append_bits(0, -len(bb) % 8)  # Note: Python's modulo on negative numbers behaves better than C family languages
		assert len(bb) % 8 == 0
		
		# Pad with alternating bytes until data capacity is reached
		for padbyte in itertools.cycle((0xEC, 0x11)):
			if len(bb) >= datacapacitybits:
				break
			bb.append_bits(padbyte, 8)
		
		# Pack bits into bytes in big endian
		datacodewords = [0] * (len(bb) // 8)
		for (i, bit) in enumerate(bb):
			datacodewords[i >> 3] |= bit << (7 - (i & 7))
		
		# Create the QR Code object
		return QrCode(version, ecl, datacodewords, mask)
	
	
	# ---- Constructor (low level) ----
	
	def __init__(self, version, errcorlvl, datacodewords, mask):
		"""Creates a new QR Code with the given version number,
		error correction level, data codeword bytes, and mask number.
		This is a low-level API that most users should not use directly.
		A mid-level API is the encode_segments() function."""
		
		# Check scalar arguments and set fields
		if not (QrCode.MIN_VERSION <= version <= QrCode.MAX_VERSION):
			raise ValueError("Version value out of range")
		if not (-1 <= mask <= 7):
			raise ValueError("Mask value out of range")
		if not isinstance(errcorlvl, QrCode.Ecc):
			raise TypeError("QrCode.Ecc expected")
		
		# The version number of this QR Code, which is between 1 and 40 (inclusive).
		# This determines the size of this barcode.
		self._version = version
		
		# The width and height of this QR Code, measured in modules, between
		# 21 and 177 (inclusive). This is equal to version * 4 + 17.
		self._size = version * 4 + 17
		
		# The error correction level used in this QR Code.
		self._errcorlvl = errcorlvl
		
		# Initialize both grids to be size*size arrays of Boolean false
		# The modules of this QR Code (False = white, True = black).
		# Immutable after constructor finishes. Accessed through get_module().
		self._modules    = [[False] * self._size for _ in range(self._size)]  # Initially all white
		# Indicates function modules that are not subjected to masking. Discarded when constructor finishes
		self._isfunction = [[False] * self._size for _ in range(self._size)]
		
		# Compute ECC, draw modules
		self._draw_function_patterns()
		allcodewords = self._add_ecc_and_interleave(datacodewords)
		self._draw_codewords(allcodewords)
		
		# Do masking
		if mask == -1:  # Automatically choose best mask
			minpenalty = 1 << 32
			for i in range(8):
				self._apply_mask(i)
				self._draw_format_bits(i)
				penalty = self._get_penalty_score()
				if penalty < minpenalty:
					mask = i
					minpenalty = penalty
				self._apply_mask(i)  # Undoes the mask due to XOR
		assert 0 <= mask <= 7
		self._apply_mask(mask)  # Apply the final choice of mask
		self._draw_format_bits(mask)  # Overwrite old format bits
		
		# The index of the mask pattern used in this QR Code, which is between 0 and 7 (inclusive).
		# Even if a QR Code is created with automatic masking requested (mask = -1),
		# the resulting object still has a mask value between 0 and 7.
		self._mask = mask
		
		del self._isfunction
	
	
	# ---- Accessor methods ----
	
	def get_version(self):
		"""Returns this QR Code's version number, in the range [1, 40]."""
		return self._version
	
	def get_size(self):
		"""Returns this QR Code's size, in the range [21, 177]."""
		return self._size
	
	def get_error_correction_level(self):
		"""Returns this QR Code's error correction level."""
		return self._errcorlvl
	
	def get_mask(self):
		"""Returns this QR Code's mask, in the range [0, 7]."""
		return self._mask
	
	def get_module(self, x, y):
		"""Returns the color of the module (pixel) at the given coordinates, which is False
		for white or True for black. The top left corner has the coordinates (x=0, y=0).
		If the given coordinates are out of bounds, then False (white) is returned."""
		return (0 <= x < self._size) and (0 <= y < self._size) and self._modules[y][x]
	
	
	# ---- Public instance methods ----
	
	def to_svg_str(self, border):
		"""Returns a string of SVG code for an image depicting this QR Code, with the given number
		of border modules. The string always uses Unix newlines (\n), regardless of the platform."""
		if border < 0:
			raise ValueError("Border must be non-negative")
		parts = []
		for y in range(self._size):
			for x in range(self._size):
				if self.get_module(x, y):
					parts.append("M{},{}h1v1h-1z".format(x + border, y + border))
		return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg xmlns="http://www.w3.org/2000/svg" version="1.1" viewBox="0 0 {0} {0}" stroke="none">
	<rect width="100%" height="100%" fill="#FFFFFF"/>
	<path d="{1}" fill="#000000"/>
</svg>
""".format(self._size + border * 2, " ".join(parts))
	
	
	# ---- Private helper methods for constructor: Drawing function modules ----
	
	def _draw_function_patterns(self):
		"""Reads this object's version field, and draws and marks all function modules."""
		# Draw horizontal and vertical timing patterns
		for i in range(self._size):
			self._set_function_module(6, i, i % 2 == 0)
			self._set_function_module(i, 6, i % 2 == 0)
		
		# Draw 3 finder patterns (all corners except bottom right; overwrites some timing modules)
		self._draw_finder_pattern(3, 3)
		self._draw_finder_pattern(self._size - 4, 3)
		self._draw_finder_pattern(3, self._size - 4)
		
		# Draw numerous alignment patterns
		alignpatpos = self._get_alignment_pattern_positions()
		numalign = len(alignpatpos)
		skips = ((0, 0), (0, numalign - 1), (numalign - 1, 0))
		for i in range(numalign):
			for j in range(numalign):
				if (i, j) not in skips:  # Don't draw on the three finder corners
					self._draw_alignment_pattern(alignpatpos[i], alignpatpos[j])
		
		# Draw configuration data
		self._draw_format_bits(0)  # Dummy mask value; overwritten later in the constructor
		self._draw_version()
	
	
	def _draw_format_bits(self, mask):
		"""Draws two copies of the format bits (with its own error correction code)
		based on the given mask and this object's error correction level field."""
		# Calculate error correction code and pack bits
		data = self._errcorlvl.formatbits << 3 | mask  # errCorrLvl is uint2, mask is uint3
		rem = data
		for _ in range(10):
			rem = (rem << 1) ^ ((rem >> 9) * 0x537)
		bits = (data << 10 | rem) ^ 0x5412  # uint15
		assert bits >> 15 == 0
		
		# Draw first copy
		for i in range(0, 6):
			self._set_function_module(8, i, _get_bit(bits, i))
		self._set_function_module(8, 7, _get_bit(bits, 6))
		self._set_function_module(8, 8, _get_bit(bits, 7))
		self._set_function_module(7, 8, _get_bit(bits, 8))
		for i in range(9, 15):
			self._set_function_module(14 - i, 8, _get_bit(bits, i))
		
		# Draw second copy
		for i in range(0, 8):
			self._set_function_module(self._size - 1 - i, 8, _get_bit(bits, i))
		for i in range(8, 15):
			self._set_function_module(8, self._size - 15 + i, _get_bit(bits, i))
		self._set_function_module(8, self._size - 8, True)  # Always black
	
	
	def _draw_version(self):
		"""Draws two copies of the version bits (with its own error correction code),
		based on this object's version field, iff 7 <= version <= 40."""
		if self._version < 7:
			return
		
		# Calculate error correction code and pack bits
		rem = self._version  # version is uint6, in the range [7, 40]
		for _ in range(12):
			rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
		bits = self._version << 12 | rem  # uint18
		assert bits >> 18 == 0
		
		# Draw two copies
		for i in range(18):
			bit = _get_bit(bits, i)
			a = self._size - 11 + i % 3
			b = i // 3
			self._set_function_module(a, b, bit)
			self._set_function_module(b, a, bit)
	
	
	def _draw_finder_pattern(self, x, y):
		"""Draws a 9*9 finder pattern including the border separator,
		with the center module at (x, y). Modules can be out of bounds."""
		for dy in range(-4, 5):
			for dx in range(-4, 5):
				xx, yy = x + dx, y + dy
				if (0 <= xx < self._size) and (0 <= yy < self._size):
					# Chebyshev/infinity norm
					self._set_function_module(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
	
	
	def _draw_alignment_pattern(self, x, y):
		"""Draws a 5*5 alignment pattern, with the center module
		at (x, y). All modules must be in bounds."""
		for dy in range(-2, 3):
			for dx in range(-2, 3):
				self._set_function_module(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)
	
	
	def _set_function_module(self, x, y, isblack):
		"""Sets the color of a module and marks it as a function module.
		Only used by the constructor. Coordinates must be in bounds."""
		assert type(isblack) is bool
		self._modules[y][x] = isblack
		self._isfunction[y][x] = True
	
	
	# ---- Private helper methods for constructor: Codewords and masking ----
	
	def _add_ecc_and_interleave(self, data):
		"""Returns a new byte string representing the given data with the appropriate error correction
		codewords appended to it, based on this object's version and error correction level."""
		version = self._version
		assert len(data) == QrCode._get_num_data_codewords(version, self._errcorlvl)
		
		# Calculate parameter numbers
		numblocks = QrCode._NUM_ERROR_CORRECTION_BLOCKS[self._errcorlvl.ordinal][version]
		blockecclen = QrCode._ECC_CODEWORDS_PER_BLOCK  [self._errcorlvl.ordinal][version]
		rawcodewords = QrCode._get_num_raw_data_modules(version) // 8
		numshortblocks = numblocks - rawcodewords % numblocks
		shortblocklen = rawcodewords // numblocks
		
		# Split data into blocks and append ECC to each block
		blocks = []
		rs = _ReedSolomonGenerator(blockecclen)
		k = 0
		for i in range(numblocks):
			dat = data[k : k + shortblocklen - blockecclen + (0 if i < numshortblocks else 1)]
			k += len(dat)
			ecc = rs.get_remainder(dat)
			if i < numshortblocks:
				dat.append(0)
			blocks.append(dat + ecc)
		assert k == len(data)
		
		# Interleave (not concatenate) the bytes from every block into a single sequence
		result = []
		for i in range(len(blocks[0])):
			for (j, blk) in enumerate(blocks):
				# Skip the padding byte in short blocks
				if i != shortblocklen - blockecclen or j >= numshortblocks:
					result.append(blk[i])
		assert len(result) == rawcodewords
		return result
	
	
	def _draw_codewords(self, data):
		"""Draws the given sequence of 8-bit codewords (data and error correction) onto the entire
		data area of this QR Code. Function modules need to be marked off before this is called."""
		assert len(data) == QrCode._get_num_raw_data_modules(self._version) // 8
		
		i = 0  # Bit index into the data
		# Do the funny zigzag scan
		for right in range(self._size - 1, 0, -2):  # Index of right column in each column pair
			if right <= 6:
				right -= 1
			for vert in range(self._size):  # Vertical counter
				for j in range(2):
					x = right - j  # Actual x coordinate
					upward = (right + 1) & 2 == 0
					y = (self._size - 1 - vert) if upward else vert  # Actual y coordinate
					if not self._isfunction[y][x] and i < len(data) * 8:
						self._modules[y][x] = _get_bit(data[i >> 3], 7 - (i & 7))
						i += 1
					# If this QR Code has any remainder bits (0 to 7), they were assigned as
					# 0/false/white by the constructor and are left unchanged by this method
		assert i == len(data) * 8
	
	
	def _apply_mask(self, mask):
		"""XORs the codeword modules in this QR Code with the given mask pattern.
		The function modules must be marked and the codeword bits must be drawn
		before masking. Due to the arithmetic of XOR, calling applyMask() with
		the same mask value a second time will undo the mask. A final well-formed
		QR Code needs exactly one (not zero, two, etc.) mask applied."""
		if not (0 <= mask <= 7):
			raise ValueError("Mask value out of range")
		masker = QrCode._MASK_PATTERNS[mask]
		for y in range(self._size):
			for x in range(self._size):
				self._modules[y][x] ^= (masker(x, y) == 0) and (not self._isfunction[y][x])
	
	
	def _get_penalty_score(self):
		"""Calculates and returns the penalty score based on state of this QR Code's current modules.
		This is used by the automatic mask choice algorithm to find the mask pattern that yields the lowest score."""
		result = 0
		size = self._size
		modules = self._modules
		
		# Adjacent modules in row having same color, and finder-like patterns
		for y in range(size):
			runhistory = collections.deque([0] * 7)
			color = False
			runx = 0
			for x in range(size):
				if modules[y][x] == color:
					runx += 1
					if runx == 5:
						result += QrCode._PENALTY_N1
					elif runx > 5:
						result += 1
				else:
					runhistory.appendleft(runx)
					if not color and QrCode.has_finder_like_pattern(runhistory):
						result += QrCode._PENALTY_N3
					color = modules[y][x]
					runx = 1
			runhistory.appendleft(runx)
			if color:
				runhistory.appendleft(0)  # Dummy run of white
			if QrCode.has_finder_like_pattern(runhistory):
				result += QrCode._PENALTY_N3
		# Adjacent modules in column having same color, and finder-like patterns
		for x in range(size):
			runhistory = collections.deque([0] * 7)
			color = False
			runy = 0
			for y in range(size):
				if modules[y][x] == color:
					runy += 1
					if runy == 5:
						result += QrCode._PENALTY_N1
					elif runy > 5:
						result += 1
				else:
					runhistory.appendleft(runy)
					if not color and QrCode.has_finder_like_pattern(runhistory):
						result += QrCode._PENALTY_N3
					color = modules[y][x]
					runy = 1
			runhistory.appendleft(runy)
			if color:
				runhistory.appendleft(0)  # Dummy run of white
			if QrCode.has_finder_like_pattern(runhistory):
				result += QrCode._PENALTY_N3
		
		# 2*2 blocks of modules having same color
		for y in range(size - 1):
			for x in range(size - 1):
				if modules[y][x] == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
					result += QrCode._PENALTY_N2
		
		# Balance of black and white modules
		black = sum((1 if cell else 0) for row in modules for cell in row)
		total = size**2  # Note that size is odd, so black/total != 1/2
		# Compute the smallest integer k >= 0 such that (45-5k)% <= black/total <= (55+5k)%
		k = (abs(black * 20 - total * 10) + total - 1) // total - 1
		result += k * QrCode._PENALTY_N4
		return result
	
	
	# ---- Private helper functions ----
	
	def _get_alignment_pattern_positions(self):
		"""Returns an ascending list of positions of alignment patterns for this version number.
		Each position is in the range [0,177), and are used on both the x and y axes.
		This could be implemented as lookup table of 40 variable-length lists of integers."""
		ver = self._version
		if ver == 1:
			return []
		else:
			numalign = ver // 7 + 2
			step = 26 if (ver == 32) else \
				(ver*4 + numalign*2 + 1) // (numalign*2 - 2) * 2
			result = [(self._size - 7 - i * step) for i in range(numalign - 1)] + [6]
			return list(reversed(result))
	
	
	@staticmethod
	def _get_num_raw_data_modules(ver):
		"""Returns the number of data bits that can be stored in a QR Code of the given version number, after
		all function modules are excluded. This includes remainder bits, so it might not be a multiple of 8.
		The result is in the range [208, 29648]. This could be implemented as a 40-entry lookup table."""
		if not (QrCode.MIN_VERSION <= ver <= QrCode.MAX_VERSION):
			raise ValueError("Version number out of range")
		result = (16 * ver + 128) * ver + 64
		if ver >= 2:
			numalign = ver // 7 + 2
			result -= (25 * numalign - 10) * numalign - 55
			if ver >= 7:
				result -= 36
		return result
	
	
	@staticmethod
	def _get_num_data_codewords(ver, ecl):
		"""Returns the number of 8-bit data (i.e. not error correction) codewords contained in any
		QR Code of the given version number and error correction level, with remainder bits discarded.
		This stateless pure function could be implemented as a (40*4)-cell lookup table."""
		return QrCode._get_num_raw_data_modules(ver) // 8 \
			- QrCode._ECC_CODEWORDS_PER_BLOCK    [ecl.ordinal][ver] \
			* QrCode._NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][ver]
	
	
	@staticmethod
	def has_finder_like_pattern(runhistory):
		runhistory = list(runhistory)
		n = runhistory[1]
		return n > 0 and n == runhistory[2] == runhistory[4] == runhistory[5] \
			and runhistory[3] == n * 3 and max(runhistory[0], runhistory[6]) >= n * 4
	
	
	# ---- Constants and tables ----
	
	MIN_VERSION =  1  # The minimum version number supported in the QR Code Model 2 standard
	MAX_VERSION = 40  # The maximum version number supported in the QR Code Model 2 standard
	
	# For use in getPenaltyScore(), when evaluating which mask is best.
	_PENALTY_N1 =  3
	_PENALTY_N2 =  3
	_PENALTY_N3 = 40
	_PENALTY_N4 = 10
	
	_ECC_CODEWORDS_PER_BLOCK = (
		# Version: (note that index 0 is for padding, and is set to an illegal value)
		#   0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40    Error correction level
		(None,  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Low
		(None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),  # Medium
		(None, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Quartile
		(None, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30))  # High
	
	_NUM_ERROR_CORRECTION_BLOCKS = (
		# Version: (note that index 0 is for padding, and is set to an illegal value)
		#   0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40    Error correction level
		(None, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4,  4,  4,  4,  4,  6,  6,  6,  6,  7,  8,  8,  9,  9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),  # Low
		(None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5,  5,  8,  9,  9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),  # Medium
		(None, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8,  8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),  # Quartile
		(None, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81))  # High
	
	_MASK_PATTERNS = (
		(lambda x, y:  (x + y) % 2                  ),
		(lambda x, y:  y % 2                        ),
		(lambda x, y:  x % 3                        ),
		(lambda x, y:  (x + y) % 3                  ),
		(lambda x, y:  (x // 3 + y // 2) % 2        ),
		(lambda x, y:  x * y % 2 + x * y % 3        ),
		(lambda x, y:  (x * y % 2 + x * y % 3) % 2  ),
		(lambda x, y:  ((x + y) % 2 + x * y % 3) % 2),
	)
	
	
	# ---- Public helper enumeration ----
	
	class Ecc(object):
		"""The error correction level in a QR Code symbol. Immutable."""
		# Private constructor
		def __init__(self, i, fb):
			self.ordinal = i  # (Public) In the range 0 to 3 (unsigned 2-bit integer)
			self.formatbits = fb  # (Package-private) In the range 0 to 3 (unsigned 2-bit integer)
	
	# Public constants. Create them outside the class.
	Ecc.LOW      = Ecc(0, 1)  # The QR Code can tolerate about  7% erroneous codewords
	Ecc.MEDIUM   = Ecc(1, 0)  # The QR Code can tolerate about 15% erroneous codewords
	Ecc.QUARTILE = Ecc(2, 3)  # The QR Code can tolerate about 25% erroneous codewords
	Ecc.HIGH     = Ecc(3, 2)  # The QR Code can tolerate about 30% erroneous codewords



# ---- Data segment class ----

class QrSegment(object):
	"""A segment of character/binary/control data in a QR Code symbol.
	Instances of this class are immutable.
	The mid-level way to create a segment is to take the payload data
	and call a static factory function such as QrSegment.make_numeric().
	The low-level way to create a segment is to custom-make the bit buffer
	and call the QrSegment() constructor with appropriate values.
	This segment class imposes no length restrictions, but QR Codes have restrictions.
	Even in the most favorable conditions, a QR Code can only hold 7089 characters of data.
	Any segment longer than this is meaningless for the purpose of generating QR Codes."""
	
	# ---- Static factory functions (mid level) ----
	
	@staticmethod
	def make_bytes(data):
		"""Returns a segment representing the given binary data encoded in byte mode.
		All input byte lists are acceptable. Any text string can be converted to
		UTF-8 bytes (s.encode("UTF-8")) and encoded as a byte mode segment."""
		py3 = sys.version_info[0] >= 3
		if (py3 and isinstance(data, str)) or (not py3 and isinstance(data, unicode)):
			raise TypeError("Byte string/list expected")
		if not py3 and isinstance(data, str):
			data = bytearray(data)
		bb = _BitBuffer()
		for b in data:
			bb.append_bits(b, 8)
		return QrSegment(QrSegment.Mode.BYTE, len(data), bb)
	
	
	@staticmethod
	def make_numeric(digits):
		"""Returns a segment representing the given string of decimal digits encoded in numeric mode."""
		if QrSegment.NUMERIC_REGEX.match(digits) is None:
			raise ValueError("String contains non-numeric characters")
		bb = _BitBuffer()
		i = 0
		while i < len(digits):  # Consume up to 3 digits per iteration
			n = min(len(digits) - i, 3)
			bb.append_bits(int(digits[i : i + n]), n * 3 + 1)
			i += n
		return QrSegment(QrSegment.Mode.NUMERIC, len(digits), bb)
	
	
	@staticmethod
	def make_alphanumeric(text):
		"""Returns a segment representing the given text string encoded in alphanumeric mode.
		The characters allowed are: 0 to 9, A to Z (uppercase only), space,
		dollar, percent, asterisk, plus, hyphen, period, slash, colon."""
		if QrSegment.ALPHANUMERIC_REGEX.match(text) is None:
			print(text, len(text))
			raise ValueError("String contains unencodable characters in alphanumeric mode")
		bb = _BitBuffer()
		for i in range(0, len(text) - 1, 2):  # Process groups of 2
			temp = QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[i]] * 45
			temp += QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[i + 1]]
			bb.append_bits(temp, 11)
		if len(text) % 2 > 0:  # 1 character remaining
			bb.append_bits(QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[-1]], 6)
		return QrSegment(QrSegment.Mode.ALPHANUMERIC, len(text), bb)
	
	
	@staticmethod
	def make_segments(text):
		"""Returns a new mutable list of zero or more segments to represent the given Unicode text string.
		The result may use various segment modes and switch modes to optimize the length of the bit stream."""
		if not (isinstance(text, str) or (sys.version_info[0] < 3 and isinstance(text, unicode))):
			raise TypeError("Text string expected")
		
		# Select the most efficient segment encoding automatically
		if text == "":
			return []
		elif QrSegment.NUMERIC_REGEX.match(text) is not None:
			return [QrSegment.make_numeric(text)]
		elif QrSegment.ALPHANUMERIC_REGEX.match(text) is not None:
			return [QrSegment.make_alphanumeric(text)]
		else:
			return [QrSegment.make_bytes(text.encode("UTF-8"))]
	
	
	@staticmethod
	def make_eci(assignval):
		"""Returns a segment representing an Extended Channel Interpretation
		(ECI) designator with the given assignment value."""
		bb = _BitBuffer()
		if assignval < 0:
			raise ValueError("ECI assignment value out of range")
		elif assignval < (1 << 7):
			bb.append_bits(assignval, 8)
		elif assignval < (1 << 14):
			bb.append_bits(2, 2)
			bb.append_bits(assignval, 14)
		elif assignval < 1000000:
			bb.append_bits(6, 3)
			bb.append_bits(assignval, 21)
		else:
			raise ValueError("ECI assignment value out of range")
		return QrSegment(QrSegment.Mode.ECI, 0, bb)
	
	
	# ---- Constructor (low level) ----
	
	def __init__(self, mode, numch, bitdata):
		"""Creates a new QR Code segment with the given attributes and data.
		The character count (numch) must agree with the mode and the bit buffer length,
		but the constraint isn't checked. The given bit buffer is cloned and stored."""
		if not isinstance(mode, QrSegment.Mode):
			raise TypeError("QrSegment.Mode expected")
		if numch < 0:
			raise ValueError()
		
		# The mode indicator of this segment. Accessed through get_mode().
		self._mode = mode
		
		# The length of this segment's unencoded data. Measured in characters for
		# numeric/alphanumeric/kanji mode, bytes for byte mode, and 0 for ECI mode.
		# Always zero or positive. Not the same as the data's bit length.
		# Accessed through get_num_chars().
		self._numchars = numch
		
		# The data bits of this segment. Accessed through get_data().
		self._bitdata = list(bitdata)  # Make defensive copy
	
	
	# ---- Accessor methods ----
	
	def get_mode(self):
		"""Returns the mode field of this segment."""
		return self._mode
	
	def get_num_chars(self):
		"""Returns the character count field of this segment."""
		return self._numchars
	
	def get_data(self):
		"""Returns a new copy of the data bits of this segment."""
		return list(self._bitdata)  # Make defensive copy
	
	
	# Package-private function
	@staticmethod
	def get_total_bits(segs, version):
		"""Calculates the number of bits needed to encode the given segments at
		the given version. Returns a non-negative number if successful. Otherwise
		returns None if a segment has too many characters to fit its length field."""
		result = 0
		for seg in segs:
			ccbits = seg.get_mode().num_char_count_bits(version)
			if seg.get_num_chars() >= (1 << ccbits):
				return None  # The segment's length doesn't fit the field's bit width
			result += 4 + ccbits + len(seg._bitdata)
		return result
	
	
	# ---- Constants ----
	
	# (Public) Describes precisely all strings that are encodable in numeric mode.
	# To test whether a string s is encodable: ok = NUMERIC_REGEX.fullmatch(s) is not None
	# A string is encodable iff each character is in the range 0 to 9.
	NUMERIC_REGEX = re.compile(r"[0-9]*$")
	
	# (Public) Describes precisely all strings that are encodable in alphanumeric mode.
	# To test whether a string s is encodable: ok = ALPHANUMERIC_REGEX.fullmatch(s) is not None
	# A string is encodable iff each character is in the following set: 0 to 9, A to Z
	# (uppercase only), space, dollar, percent, asterisk, plus, hyphen, period, slash, colon.

	ALPHANUMERIC_REGEX = re.compile(r"[A-Z0-9 $%*+./:-]*$")
	
	# (Private) Dictionary of "0"->0, "A"->10, "$"->37, etc.
	_ALPHANUMERIC_ENCODING_TABLE = {ch: i for (i, ch) in enumerate("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:")}
	
	
	# ---- Public helper enumeration ----
	
	class Mode(object):
		"""Describes how a segment's data bits are interpreted. Immutable."""
		
		# Private constructor
		def __init__(self, modebits, charcounts):
			self._modebits = modebits  # The mode indicator bits, which is a uint4 value (range 0 to 15)
			self._charcounts = charcounts  # Number of character count bits for three different version ranges
		
		# Package-private method
		def get_mode_bits(self):
			"""Returns an unsigned 4-bit integer value (range 0 to 15) representing the mode indicator bits for this mode object."""
			return self._modebits
		
		# Package-private method
		def num_char_count_bits(self, ver):
			"""Returns the bit width of the character count field for a segment in this mode
			in a QR Code at the given version number. The result is in the range [0, 16]."""
			return self._charcounts[(ver + 7) // 17]
	
	# Public constants. Create them outside the class.
	Mode.NUMERIC      = Mode(0x1, (10, 12, 14))
	Mode.ALPHANUMERIC = Mode(0x2, ( 9, 11, 13))
	Mode.BYTE         = Mode(0x4, ( 8, 16, 16))
	Mode.KANJI        = Mode(0x8, ( 8, 10, 12))
	Mode.ECI          = Mode(0x7, ( 0,  0,  0))



# ---- Private helper classes ----

class _ReedSolomonGenerator(object):
	"""Computes the Reed-Solomon error correction codewords for a sequence of data codewords
	at a given degree. Objects are immutable, and the state only depends on the degree.
	This class exists because each data block in a QR Code shares the same the divisor polynomial."""
	
	def __init__(self, degree):
		"""Creates a Reed-Solomon ECC generator for the given degree. This could be implemented
		as a lookup table over all possible parameter values, instead of as an algorithm."""
		if degree < 1 or degree > 255:
			raise ValueError("Degree out of range")
		
		# Start with the monomial x^0
		self.coefficients = [0] * (degree - 1) + [1]
		
		# Compute the product polynomial (x - r^0) * (x - r^1) * (x - r^2) * ... * (x - r^{degree-1}),
		# drop the highest term, and store the rest of the coefficients in order of descending powers.
		# Note that r = 0x02, which is a generator element of this field GF(2^8/0x11D).
		root = 1
		for _ in range(degree):  # Unused variable i
			# Multiply the current product by (x - r^i)
			for j in range(degree):
				self.coefficients[j] = _ReedSolomonGenerator._multiply(self.coefficients[j], root)
				if j + 1 < degree:
					self.coefficients[j] ^= self.coefficients[j + 1]
			root = _ReedSolomonGenerator._multiply(root, 0x02)
	
	
	def get_remainder(self, data):
		"""Computes and returns the Reed-Solomon error correction codewords for the given
		sequence of data codewords. The returned object is always a new byte list.
		This method does not alter this object's state (because it is immutable)."""
		# Compute the remainder by performing polynomial division
		result = [0] * len(self.coefficients)
		for b in data:
			factor = b ^ result.pop(0)
			result.append(0)
			for (i, coef) in enumerate(self.coefficients):
				result[i] ^= _ReedSolomonGenerator._multiply(coef, factor)
		return result
	
	
	@staticmethod
	def _multiply(x, y):
		"""Returns the product of the two given field elements modulo GF(2^8/0x11D). The arguments and result
		are unsigned 8-bit integers. This could be implemented as a lookup table of 256*256 entries of uint8."""
		if x >> 8 != 0 or y >> 8 != 0:
			raise ValueError("Byte out of range")
		# Russian peasant multiplication
		z = 0
		for i in reversed(range(8)):
			z = (z << 1) ^ ((z >> 7) * 0x11D)
			z ^= ((y >> i) & 1) * x
		assert z >> 8 == 0
		return z



class _BitBuffer(list):
	"""An appendable sequence of bits (0s and 1s). Mainly used by QrSegment."""
	
	def append_bits(self, val, n):
		"""Appends the given number of low-order bits of the given
		value to this buffer. Requires n >= 0 and 0 <= val < 2^n."""
		if n < 0 or val >> n != 0:
			raise ValueError("Value out of range")
		self.extend(((val >> i) & 1) for i in reversed(range(n)))


def _get_bit(x, i):
	"""Returns true iff the i'th bit of x is set to 1."""
	return (x >> i) & 1 != 0



class DataTooLongError(ValueError):
	"""Raised when the supplied data does not fit any QR Code version. Ways to handle this exception include:
	- Decrease the error correction level if it was greater than Ecc.LOW.
	- If the encode_segments() function was called with a maxversion argument, then increase
	  it if it was less than QrCode.MAX_VERSION. (This advice does not apply to the other
	  factory functions because they search all versions up to QrCode.MAX_VERSION.)
	- Split the text data into better or optimal segments in order to reduce the number of bits required.
	- Change the text or binary data to be shorter.
	- Change the text to fit the character set of a particular segment mode (e.g. alphanumeric).
	- Propagate the error upward to the caller/user."""
	pass

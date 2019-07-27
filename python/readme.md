# QR Code generator library: Python

This fork is designed to extend Python support to include MicroPython. The goal was to be able to use the same Python code for CPython and MicroPython.

## Porting to MicroPython

MicroPython has some differences to "big" Python.  Here is how a single codebase was achieved.

### `__future__`

* MicroPython doesn't have support for the `__future__` module (since it's always been based on 3.x)
* Currently references to `__future__` have been removed but it would be better to add a stub in MicroPython to ignore future functions
  * A side effect is that this code no longer operates on Python 2.x

### Regular expressions

* MicroPython implements a subset of the full regex libary and, to make this clearer, names the module `ure` ('micro' re)
  * So a slight change required to `import ure as re`
* `\Z` is _not_ supported
  * But on non-multiline matches `\Z` is equivalent to "$" which _is_ supported; so change all references of `\Z` to `$`

### sys

* In this application `sys` is used to determine if the environment is a Python version >=3
* There _is_ a `sys` module in MicroPython but it differs in some ways to modern Python implementations
  * Specifically, `sys.version_info` is implemented as a tuple in MicroPython but a class in Python
    * And so `sys.version_info.major`, used to check for '3', doesn't exist on MicroPython
  * However, `sys.version_info[0]` is equivalent to `sys.version_info.major` so can use that instead

### deque

* MicroPython has a built-in `deque`, written in C
  * But it is a _very_ small subset - in particular, no `appendleft` and no subscripting support
* [micropython-lib](https://github.com/micropython/micropython-lib) contains an alternative [deque](https://github.com/micropython/micropython-lib/tree/master/collections.deque)
  * Used `upip` to install: `upip.install('micropython-collections.deque')`
  * _Most_ features are implemented but it's pure (Micro)Python and wraps a `list`
    * So performance is questionable...
  * Still some differences to CPython deque
    * Slightly different `__init__` (2 vs 1 init params)
    * No subscripting 
    * But _does_ support `__iter__` so can convert to list when subscripting is required
      * Which is also likely to be a performance hit...

### itertools

* `itertools` is not part of core MicroPython
  * Implementation available in micropython-lib, install with `upip.install('micropython-itertools')`

## Performance

On the test device, a [TinyPICO](https://www.crowdsupply.com/unexpected-maker/tinypico) (Espressif ESP32 with 4MB PSRAM), QR codes containing short strings typically take about <2 seconds to generate. Longer strings can take more than 10 seconds. 

```python
def test(text="QR Code Test", ecc_quality=QrCode.Ecc.LOW):
    gc.collect()
    print(micropython.mem_info())
    t = utime.ticks_ms()
    qr0 = QrCode.encode_text(text, ecc_quality)
    dt = utime.ticks_diff(utime.ticks_ms(), t)
    modules = qr0._modules
    print(dt)
    gc.collect()
    print(micropython.mem_info())
```

`dt` is typically ~1.7-1.9 seconds.

### Notes on performance

* _No_ optimisations applied (yet!)
* deque implementation is likely to be very slow, and it's use is performance sensitive in this application


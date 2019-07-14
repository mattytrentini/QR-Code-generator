# QR Code generator library: Python

This fork is designed to extend the Python support to MicroPython. 

## Porting to MicroPython

o __future__
  - Removed from code
o ure
  - MicroPython has a subset of the re library known as ure
  - import ure as re
  - "\Z" is not supported
    - But on non-multiline matches is equivalent to "$" which is supported
o sys
  - Used to check if running under Python version 3
  - But Python/MicroPython differ: sys.version_info is a class/tuple
    - 'major' doesn't exist on MicroPython
  - Common to both: sys.version_info[0] == major version
o deque
  - MicroPython has a built-in deque, written in C
    - But is a very small subset (no appendleft, no subscripting)
  - Replaced with collections.deque from micropython-lib 
    - Slightly different __init__
    - No subscripting :(
    - But does support __iter__ - so can convert to list
    - Performance is likely to be poor



## Performance

See 

### Notes on performance
* No optimisations applied (yet!)
* deque implementation is likely to be slow
  - Implemented around a list in MicroPython
  - Built-in deque is implemented in C but lacking key features


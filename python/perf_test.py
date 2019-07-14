import utime
import micropython, gc
from qrcodegen import QrCode

def test(text="QR Code Test", ecc_quality=QrCode.Ecc.LOW):
    gc.collect()
    print(micropython.mem_info())
    t = utime.ticks_ms()
    dt1 = utime.ticks_diff(utime.ticks_ms(), t)
    t = utime.ticks_ms()
    qr0 = QrCode.encode_text(text, ecc_quality)
    dt2 = utime.ticks_diff(utime.ticks_ms(), t)
    t = utime.ticks_ms()
    modules = qr0._modules
    dt3 = utime.ticks_diff(utime.ticks_ms(), t)
    print(dt1, dt2, dt3)
    gc.collect()
    print(micropython.mem_info())
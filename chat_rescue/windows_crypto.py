"""AES-128-ECB with PKCS#7 via Windows CNG, no extra DLL or key extraction.

API contract: https://learn.microsoft.com/windows/win32/api/bcrypt/nf-bcrypt-bcryptencrypt
Only used for already verified offline image DAT payloads.
"""
import ctypes as C
import os


def aes_ecb(data, key, encrypt):
    if os.name != 'nt' or not isinstance(data, bytes) or not isinstance(key, bytes) or len(key) != 16:
        raise ValueError('Windows DAT 加密参数无效')
    if encrypt:
        pad = 16 - len(data) % 16
        data += bytes([pad]) * pad
    elif not data or len(data) % 16:
        raise ValueError('DAT 密文长度无效')
    lib = C.WinDLL('bcrypt.dll')
    handle, ulong, ptr = C.c_void_p, C.c_ulong, C.c_void_p
    signatures = {
        'BCryptOpenAlgorithmProvider': [C.POINTER(handle), C.c_wchar_p, C.c_wchar_p, ulong],
        'BCryptSetProperty': [handle, C.c_wchar_p, ptr, ulong, ulong],
        'BCryptGetProperty': [handle, C.c_wchar_p, ptr, ulong, C.POINTER(ulong), ulong],
        'BCryptGenerateSymmetricKey': [handle, C.POINTER(handle), ptr, ulong, ptr, ulong, ulong],
        'BCryptEncrypt': [handle, ptr, ulong, ptr, ptr, ulong, ptr, ulong, C.POINTER(ulong), ulong],
        'BCryptDecrypt': [handle, ptr, ulong, ptr, ptr, ulong, ptr, ulong, C.POINTER(ulong), ulong],
        'BCryptDestroyKey': [handle],
        'BCryptCloseAlgorithmProvider': [handle, ulong],
    }
    for name, args in signatures.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, C.c_long

    def checked(status):
        if status != 0:
            raise ValueError('Windows DAT AES 操作失败')

    algorithm, secret = handle(), handle()
    key_object = None
    try:
        checked(lib.BCryptOpenAlgorithmProvider(C.byref(algorithm), 'AES', None, 0))
        mode = C.create_unicode_buffer('ChainingModeECB')
        checked(lib.BCryptSetProperty(algorithm, 'ChainingMode', mode, C.sizeof(mode), 0))
        length, received = ulong(), ulong()
        checked(lib.BCryptGetProperty(algorithm, 'ObjectLength', C.byref(length), C.sizeof(length), C.byref(received), 0))
        if not 0 < length.value <= 1024 * 1024:
            raise ValueError('Windows AES 密钥对象长度无效')
        key_object = C.create_string_buffer(length.value)
        checked(lib.BCryptGenerateSymmetricKey(algorithm, C.byref(secret), key_object, length, key, len(key), 0))
        output, written = C.create_string_buffer(len(data)), ulong()
        fn = lib.BCryptEncrypt if encrypt else lib.BCryptDecrypt
        checked(fn(secret, data, len(data), None, None, 0, output, len(data), C.byref(written), 0))
        if written.value != len(data):
            raise ValueError('Windows AES 读回长度不符')
        result = output.raw[:written.value]
        if not encrypt:
            pad = result[-1]
            if not 1 <= pad <= 16 or result[-pad:] != bytes([pad]) * pad:
                raise ValueError('DAT 密钥或填充校验失败')
            result = result[:-pad]
        return result
    finally:
        if secret:
            lib.BCryptDestroyKey(secret)
        if key_object is not None:
            C.memset(key_object, 0, C.sizeof(key_object))
        if algorithm:
            lib.BCryptCloseAlgorithmProvider(algorithm, 0)

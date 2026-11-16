"""macOS menu bar shows the program name instead of Python."""


def mac_app_name(name):
    """macOS: show the program's own name in the menu bar (and the app menu) instead of "Python". Call before QApplication(...).
    Does nothing on Linux / Windows."""
    import sys
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p

        def send(obj, sel, *args):
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_void_p] * len(args)
            return msg(obj, objc.sel_registerName(sel.encode()), *args)

        def nsstr(s):
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
            return msg(objc.objc_getClass(b"NSString"), objc.sel_registerName(b"stringWithUTF8String:"), s.encode("utf-8"))

        bundle = send(objc.objc_getClass(b"NSBundle"), "mainBundle")
        info = send(bundle, "infoDictionary")
        if info:
            for key in ("CFBundleName", "CFBundleDisplayName"):
                send(info, "setObject:forKey:", nsstr(name), nsstr(key))
        send(send(objc.objc_getClass(b"NSProcessInfo"), "processInfo"), "setProcessName:", nsstr(name))
    except Exception:
        pass

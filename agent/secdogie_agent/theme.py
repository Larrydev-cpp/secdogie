"""Shared operator-console palette.

Tk dialogs, the launcher, and the persistent HUD all draw from this so the
packaged exe is one visual language — iOS grouped dark, one cool-gray accent,
no purple, no emoji chrome.
"""
from __future__ import annotations

import sys

# iOS grouped dark. Tap target 44px. Radius is a hint — Tk draws the panel,
# the OS compositor rounds the window when it can.
BG = "#000000"
SURFACE = "#1c1c1e"
SURFACE_2 = "#2c2c2e"
FG = "#f5f5f7"
MUTED = "#8e8e93"
SUBTLE = "#636366"
ACCENT = "#d4d4d8"
ACCENT_FG = "#000000"
DENY = "#ff453a"
OK = "#30d158"
WARN = "#ffd60a"
BORDER = "#38383a"
TAP = 44


def font(size: int = 10, *, bold: bool = False, mono: bool = False):
    if mono:
        if sys.platform == "win32":
            family = "Consolas"
        elif sys.platform == "darwin":
            family = "SF Mono"
        else:
            family = "monospace"
    elif sys.platform == "win32":
        family = "Segoe UI"
    elif sys.platform == "darwin":
        family = ".AppleSystemUIFont"
    else:
        family = "sans-serif"
    return (family, size, "bold") if bold else (family, size)


def apply_glass(root) -> None:
    """Best-effort frost + round corners via the OS compositor.

    Windows: acrylic blur-behind + DWM rounded corners.
    Darwin: dark Aqua + NSVisualEffectView when AppKit is present.
    Anywhere they cannot apply, the grouped-dark panel still shows.
    """
    if sys.platform.startswith("win"):
        _windows_acrylic(root)
        return
    if sys.platform == "darwin":
        _macos_vibrancy(root)


def apply_hud_behavior(root) -> None:
    """Stay visible, never steal the app being driven.

    Windows: WS_EX_NOACTIVATE + topmost, still on the taskbar.
    Darwin: Aqua utility style with noActivates so AXPress lands on the
    target, not on this console.
    """
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    if sys.platform == "win32":
        _windows_no_activate(root)
        return
    if sys.platform == "darwin":
        try:
            root.tk.call(
                "tk::unsupported::MacWindowStyle",
                "style",
                root._w,
                "utility",
                ("noActivates", "closeBox"),
            )
        except Exception:
            pass


def _windows_acrylic(root) -> None:
    try:
        import ctypes

        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("Flags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()

        accent = ACCENT_POLICY()
        accent.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.GradientColor = 0xCC000000  # 0xAABBGGRR near-black
        data = WINCOMPATTRDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))

        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass


def _macos_vibrancy(root) -> None:
    try:
        root.tk.call("tk::unsupported::MacWindowStyle", "appearance", root._w, "dark")
    except Exception:
        pass
    try:
        root.attributes("-alpha", 0.97)
    except Exception:
        pass
    try:
        from AppKit import (  # type: ignore
            NSApp,
            NSMakeRect,
            NSViewHeightSizable,
            NSViewWidthSizable,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialHUDWindow,
            NSVisualEffectView,
        )

        root.update_idletasks()
        app = NSApp() if callable(NSApp) else NSApp
        windows = app.windows() if app is not None else None
        nswin = windows[-1] if windows else None
        if nswin is None:
            return
        content = nswin.contentView()
        if content is None:
            return
        bounds = content.bounds()
        fx = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, bounds.size.width, bounds.size.height)
        )
        fx.setMaterial_(NSVisualEffectMaterialHUDWindow)
        fx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        fx.setState_(1)
        fx.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_positioned_relativeTo_(fx, 1, None)  # NSWindowBelow
        fx.setWantsLayer_(True)
        try:
            nswin.setTitlebarAppearsTransparent_(True)
            nswin.setOpaque_(False)
        except Exception:
            pass
    except Exception:
        pass


def _windows_no_activate(root) -> None:
    """Stay on the taskbar, never steal foreground.

    WS_EX_TOOLWINDOW is deliberately not set — that hides the window from the
    taskbar and is exactly the 'latent background process' look.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOPMOST = 0x00000008
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        HWND_TOPMOST = -1
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, (style | WS_EX_NOACTIVATE | WS_EX_TOPMOST) & ~0x00000080
        )
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
    except Exception:
        pass

"""
main.py
=======
Desktop UI for **Apex SBD – Powerlifting Progress & Analytics System**.

Built with customtkinter (rounded, dark widgets) and Matplotlib (embedded,
dark-themed charts). Calculations live in ``analytics.py`` and persistence in
``database.py``; this module only orchestrates and renders.

Run:
    pip install customtkinter matplotlib
    python main.py                   # uses apex_sbd.db next to this file
    python main.py --db my_log.db    # use (or create) a different database file
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
import tkinter.font as tkfont
import traceback
from datetime import date
from functools import lru_cache
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Mapping, Optional, Sequence

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("Missing dependency 'customtkinter'. Install with: pip install customtkinter matplotlib\n")
    raise SystemExit(1)

try:
    import matplotlib

    matplotlib.use("TkAgg")
    import matplotlib.dates as mdates
    from matplotlib import font_manager
    from matplotlib.axes import Axes
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.figure import Figure
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("Missing dependency 'matplotlib'. Install with: pip install customtkinter matplotlib\n")
    raise SystemExit(1)

import analytics as an
import database as db

APP_NAME = "Apex SBD"
APP_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Theme & typography
# ---------------------------------------------------------------------------
class Theme:
    """Colour tokens shared by every widget and chart."""

    BG = "#121212"
    SIDEBAR = "#161616"
    CARD = "#1c1c1c"
    CARD_ALT = "#232323"
    INPUT = "#141414"
    BORDER = "#2b2b2b"
    TEXT = "#ececec"
    MUTED = "#8d939c"
    SUBTLE = "#5b6068"
    ACCENT = "#2ecc71"
    ACCENT_HOVER = "#26b062"
    ACCENT_DEEP = "#1f8f4f"
    CYAN = "#00d2d3"
    CYAN_HOVER = "#00b0b1"
    ORANGE = "#ff9f43"
    ORANGE_HOVER = "#e58a32"
    VIOLET = "#a29bfe"
    RED = "#ff6b6b"
    RED_HOVER = "#e85a5a"
    ON_ACCENT = "#0c140f"
    LIFT_COLORS: dict[str, str] = {"squat": ACCENT, "bench": CYAN, "deadlift": ORANGE, "total": VIOLET}


LIFT_LABELS: dict[str, str] = {"squat": "Squat", "bench": "Bench", "deadlift": "Deadlift", "total": "Total"}
LIFT_INITIALS: dict[str, str] = {"squat": "S", "bench": "B", "deadlift": "D", "total": "T"}

# Condensed, DIN-style faces give numbers a meet-scoreboard feel; body text stays neutral.
DISPLAY_FONT_PREFERENCES: tuple[str, ...] = (
    "Bahnschrift", "DIN Alternate", "DIN Condensed", "Barlow Semi Condensed", "Roboto Condensed",
    "Archivo Narrow", "Ubuntu Condensed", "DejaVu Sans Condensed",
)
BODY_FONT_PREFERENCES: tuple[str, ...] = (
    "Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans",
)
_FONT_FAMILIES: dict[str, str] = {}


def _first_installed(preferences: Sequence[str], installed: Mapping[str, str], fallback: str) -> str:
    """Return the first family from ``preferences`` that is installed, else ``fallback``."""
    for name in preferences:
        if name.lower() in installed:
            return installed[name.lower()]
    return fallback


def resolve_font_families(root: tk.Misc) -> None:
    """Pick the display and body typefaces available on this machine (call once after root exists)."""
    installed = {name.lower(): name for name in tkfont.families(root)}
    fallback = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    _FONT_FAMILIES["body"] = _first_installed(BODY_FONT_PREFERENCES, installed, fallback)
    _FONT_FAMILIES["display"] = _first_installed(DISPLAY_FONT_PREFERENCES, installed, _FONT_FAMILIES["body"])


@lru_cache(maxsize=None)
def font(size: int, weight: str = "normal", role: str = "body") -> ctk.CTkFont:
    """Return a cached CTkFont for ``role`` (``body`` or ``display``) at ``size`` px."""
    family = _FONT_FAMILIES.get(role) or _FONT_FAMILIES.get("body")
    return ctk.CTkFont(family=family, size=size, weight=weight)


def configure_matplotlib() -> None:
    """Apply the dark theme and the preferred typeface to Matplotlib globally."""
    available = {entry.name for entry in font_manager.fontManager.ttflist}
    family = next((name for name in DISPLAY_FONT_PREFERENCES if name in available), "DejaVu Sans")
    matplotlib.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [family, "DejaVu Sans"],
        "font.size": 9.5,
        "text.color": Theme.TEXT,
        "axes.labelcolor": Theme.MUTED,
        "axes.edgecolor": Theme.BORDER,
        "xtick.color": Theme.MUTED,
        "ytick.color": Theme.MUTED,
        "legend.frameon": False,
        "figure.facecolor": Theme.CARD,
        "axes.facecolor": Theme.CARD,
    })


# ---------------------------------------------------------------------------
# Formatting & small helpers
# ---------------------------------------------------------------------------
def fmt_num(value: float, decimals: int = 2) -> str:
    """Format a number with thousands separators and no trailing zeros (``550``, ``122.5``)."""
    text = f"{value:,.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_plain(value: float) -> str:
    """Format a number for an input field (no separators, no trailing zeros)."""
    return f"{round(value, 2):g}"


def fmt_kg(value: Optional[float]) -> str:
    """Format kilograms, e.g. ``550 kg``; ``None`` becomes an em dash."""
    return "—" if value is None else f"{fmt_num(value)} kg"


def fmt_signed(value: Optional[float], unit: str = " kg") -> str:
    """Format a signed change such as ``+12.5 kg`` or ``−5 kg`` (``±0`` when unchanged)."""
    if value is None:
        return "—"
    if abs(value) < 0.005:
        return f"±0{unit}"
    sign = "+" if value > 0 else "−"
    return f"{sign}{fmt_num(abs(value))}{unit}"


def delta_color(value: Optional[float]) -> str:
    """Return green for gains, red for losses and muted grey for no change."""
    if value is None or abs(value) < 0.005:
        return Theme.MUTED
    return Theme.ACCENT if value > 0 else Theme.RED


def fmt_date(value: date) -> str:
    """Format a date as ``9 Oct 2026`` (portable across operating systems)."""
    return f"{value.day} {value:%b %Y}"


def blend(color: str, background: str, alpha: float) -> str:
    """Mix ``color`` over ``background`` with opacity ``alpha`` and return a hex colour."""
    fg = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    bg = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(f * alpha + b * (1.0 - alpha)) for f, b in zip(fg, bg)]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def safe_positive_float(text: str) -> Optional[float]:
    """Parse ``text`` as a positive finite float, returning ``None`` instead of raising."""
    try:
        value = float(str(text).strip().lower().removesuffix("kg").strip().replace(",", "."))
    except ValueError:
        return None
    return value if 0 < value < float("inf") else None


def parse_reps(text: str) -> int:
    """Parse a repetition count typed by the user (range checks happen in analytics)."""
    cleaned = str(text).strip()
    if not cleaned:
        raise ValueError("Reps is required.")
    try:
        return int(cleaned)
    except ValueError:
        raise ValueError(f"Reps must be a whole number, '{cleaned}' is not valid.") from None


def widget_scaling(widget: Any) -> float:
    """Return customtkinter's widget scaling factor for ``widget`` (1.0 if unknown)."""
    try:
        return float(ctk.ScalingTracker.get_widget_scaling(widget))
    except Exception:  # noqa: BLE001 - scaling is cosmetic, never fatal
        return 1.0


def window_scaling(window: Any) -> float:
    """Return customtkinter's window scaling factor for ``window`` (1.0 if unknown)."""
    try:
        return float(ctk.ScalingTracker.get_window_scaling(window))
    except Exception:  # noqa: BLE001
        return 1.0


def center_over(window: tk.Toplevel, master: tk.Misc) -> None:
    """Position ``window`` (at its natural size) centred over ``master``."""
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    x = master.winfo_rootx() + (master.winfo_width() - width) // 2
    y = master.winfo_rooty() + (master.winfo_height() - height) // 3
    tk.Wm.wm_geometry(window, f"+{max(x, 0)}+{max(y, 0)}")


def activate_modal(window: tk.Toplevel) -> None:
    """Raise, focus and grab input for a dialog once it is visible."""
    try:
        window.lift()
        window.focus_force()
        window.grab_set()
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Widget factories
# ---------------------------------------------------------------------------
class WrapLabel(ctk.CTkLabel):
    """CTkLabel that re-wraps its text to whatever width layout gives it.

    Place it with ``fill="x"`` (pack) or ``sticky="ew"`` (grid) so the width is
    driven by the container rather than by the text.
    """

    def __init__(self, master: Any, *, inset: int = 4, **kwargs: Any) -> None:
        """Create the label; ``inset`` is subtracted from the width before wrapping."""
        kwargs.setdefault("wraplength", 260)
        kwargs.setdefault("justify", "left")
        kwargs.setdefault("anchor", "w")
        super().__init__(master, **kwargs)
        self._inset = inset
        self._wrap = float(kwargs["wraplength"])
        tk.Misc.bind(self, "<Configure>", self._rewrap, "+")

    def _rewrap(self, event: tk.Event) -> None:
        """Update ``wraplength`` when the allocated width changes."""
        width = event.width / widget_scaling(self) - self._inset
        if width > 40 and abs(width - self._wrap) > 2:
            self._wrap = width
            self.configure(wraplength=width)


class DarkFigureCanvas(FigureCanvasTkAgg):
    """TkAgg canvas that clears its photo buffer on resize.

    Matplotlib re-uses the Tk photo when the widget is resized; if the new
    figure is a pixel narrower than the widget, a 1 px strip of stale pixels
    remains visible. Blanking the photo first leaves the dark background.
    """

    def resize(self, event: tk.Event) -> None:
        """Resize the figure and blank the backing image before it is redrawn."""
        super().resize(event)
        photo = getattr(self, "_tkphoto", None)
        if photo is not None:
            try:
                photo.blank()
            except tk.TclError:
                pass


def make_card(master: Any, **kwargs: Any) -> ctk.CTkFrame:
    """Return a rounded surface frame used for every card in the app."""
    options = {"fg_color": Theme.CARD, "corner_radius": 16, "border_width": 1, "border_color": Theme.BORDER}
    options.update(kwargs)
    return ctk.CTkFrame(master, **options)


def primary_button(
    master: Any,
    text: str,
    command: Callable[[], Any],
    *,
    color: str = Theme.ACCENT,
    hover: str = Theme.ACCENT_HOVER,
    width: int = 140,
    height: int = 40,
) -> ctk.CTkButton:
    """Return a solid call-to-action button."""
    return ctk.CTkButton(
        master, text=text, command=command, width=width, height=height, corner_radius=10,
        fg_color=color, hover_color=hover, text_color=Theme.ON_ACCENT, font=font(14, "bold"),
    )


def ghost_button(
    master: Any,
    text: str,
    command: Callable[[], Any],
    *,
    text_color: str = Theme.TEXT,
    border_color: str = Theme.BORDER,
    width: int = 110,
    height: int = 40,
) -> ctk.CTkButton:
    """Return an outlined secondary button."""
    return ctk.CTkButton(
        master, text=text, command=command, width=width, height=height, corner_radius=10,
        fg_color="transparent", hover_color=Theme.CARD_ALT, border_width=1, border_color=border_color,
        text_color=text_color, text_color_disabled=Theme.SUBTLE, font=font(14, "bold"),
    )


def styled_entry(master: Any, placeholder: str, *, width: int = 140, height: int = 40) -> ctk.CTkEntry:
    """Return a dark, rounded text entry."""
    return ctk.CTkEntry(
        master, placeholder_text=placeholder, width=width, height=height, corner_radius=10,
        border_width=1, border_color=Theme.BORDER, fg_color=Theme.INPUT, text_color=Theme.TEXT,
        placeholder_text_color=Theme.SUBTLE, font=font(14),
    )


def option_menu(master: Any, values: Sequence[str], command: Callable[[str], Any], *, width: int = 160) -> ctk.CTkOptionMenu:
    """Return a dark dropdown menu."""
    return ctk.CTkOptionMenu(
        master, values=list(values), command=command, width=width, height=40, corner_radius=10,
        fg_color=Theme.INPUT, button_color=Theme.CARD_ALT, button_hover_color=Theme.BORDER,
        text_color=Theme.TEXT, font=font(13), dropdown_font=font(13),
        dropdown_fg_color=Theme.CARD_ALT, dropdown_hover_color=Theme.BORDER, dropdown_text_color=Theme.TEXT,
    )


def segmented(master: Any, values: Sequence[str], command: Optional[Callable[[str], Any]] = None) -> ctk.CTkSegmentedButton:
    """Return a dark segmented control with an emerald selection."""
    return ctk.CTkSegmentedButton(
        master, values=list(values), command=command, height=36, corner_radius=10, font=font(13, "bold"),
        fg_color=Theme.INPUT, selected_color=Theme.ACCENT_DEEP, selected_hover_color=Theme.ACCENT_HOVER,
        unselected_color=Theme.INPUT, unselected_hover_color=Theme.CARD_ALT, text_color=Theme.TEXT,
    )


def divider(master: Any) -> tk.Frame:
    """Return a 1 px hairline used to separate rows inside a card."""
    return tk.Frame(master, height=1, background=Theme.BORDER, borderwidth=0, highlightthickness=0)


def card_header(master: Any, title: str, subtitle: str = "") -> None:
    """Pack a title and optional subtitle at the top of a card."""
    ctk.CTkLabel(master, text=title, font=font(17, "bold"), text_color=Theme.TEXT, anchor="w").pack(
        fill="x", padx=22, pady=(20, 0))
    if subtitle:
        WrapLabel(master, text=subtitle, font=font(12), text_color=Theme.MUTED).pack(fill="x", padx=22, pady=(2, 0))


def style_axes(ax: Axes) -> None:
    """Apply the dark dashboard look to a Matplotlib ``Axes``."""
    ax.set_facecolor(Theme.CARD)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(Theme.BORDER)
    ax.tick_params(colors=Theme.MUTED, labelsize=9, length=0, pad=6)
    ax.yaxis.grid(True, color=Theme.BORDER, linewidth=0.8)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)


# ---------------------------------------------------------------------------
# Toast notifications & dialogs
# ---------------------------------------------------------------------------
class ToastManager:
    """Non-blocking, auto-dismissing notifications stacked in the bottom-right corner."""

    STYLES: dict[str, tuple[str, str, str]] = {
        "success": (Theme.ACCENT, "✓", "Done"),
        "error": (Theme.RED, "✕", "Something needs fixing"),
        "warning": (Theme.ORANGE, "!", "Heads up"),
        "info": (Theme.CYAN, "i", "Info"),
    }
    MAX_TOASTS = 4
    MARGIN = 24
    GAP = 10

    def __init__(self, root: ctk.CTk) -> None:
        """Attach the manager to the main window ``root``."""
        self.root = root
        self._toasts: list[ctk.CTkFrame] = []

    def show(self, message: str, kind: str = "info", title: Optional[str] = None, duration_ms: int = 4500) -> None:
        """Display a toast; ``kind`` is ``success``, ``error``, ``warning`` or ``info``."""
        color, glyph, default_title = self.STYLES.get(kind, self.STYLES["info"])
        toast = ctk.CTkFrame(self.root, fg_color=Theme.CARD_ALT, corner_radius=12, border_width=1,
                             border_color=blend(color, Theme.CARD_ALT, 0.55))
        toast.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(toast, text=glyph, width=26, height=26, corner_radius=13, fg_color=color,
                     text_color=Theme.ON_ACCENT, font=font(13, "bold")).grid(
            row=0, column=0, rowspan=2, padx=(14, 10), pady=14, sticky="n")
        ctk.CTkLabel(toast, text=title or default_title, font=font(13, "bold"), text_color=Theme.TEXT,
                     anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 0))
        ctk.CTkLabel(toast, text=message, font=font(12), text_color=Theme.MUTED, anchor="w", justify="left",
                     wraplength=320).grid(row=1, column=1, sticky="ew", pady=(2, 12))
        ctk.CTkButton(toast, text="✕", width=24, height=24, corner_radius=6, fg_color="transparent",
                      hover_color=Theme.BORDER, text_color=Theme.MUTED, font=font(12),
                      command=lambda t=toast: self.dismiss(t)).grid(row=0, column=2, padx=(8, 10), pady=(10, 0),
                                                                    sticky="ne")
        self._toasts.append(toast)
        while len(self._toasts) > self.MAX_TOASTS:
            self.dismiss(self._toasts[0])
        toast.place(relx=1.0, rely=1.0, x=-self.MARGIN, y=-self.MARGIN, anchor="se")
        toast.lift()
        self.root.after(20, self._restack)
        self.root.after(duration_ms, lambda t=toast: self.dismiss(t))

    def dismiss(self, toast: ctk.CTkFrame) -> None:
        """Remove ``toast`` (no-op if it is already gone) and restack the rest."""
        if toast not in self._toasts:
            return
        self._toasts.remove(toast)
        try:
            toast.destroy()
        except tk.TclError:
            pass
        self._restack()

    def _restack(self) -> None:
        """Re-position live toasts so the newest sits at the bottom."""
        offset = float(self.MARGIN)
        scale = widget_scaling(self.root)
        for toast in reversed(self._toasts):
            try:
                toast.place(relx=1.0, rely=1.0, x=-self.MARGIN, y=-offset, anchor="se")
                toast.lift()
                offset += toast.winfo_reqheight() / scale + self.GAP
            except tk.TclError:
                continue


class ConfirmDialog(ctk.CTkToplevel):
    """Modal yes/no dialog that calls ``on_confirm`` only when the user agrees."""

    def __init__(
        self,
        master: ctk.CTk,
        title: str,
        message: str,
        on_confirm: Callable[[], None],
        *,
        confirm_text: str = "Confirm",
        danger: bool = True,
    ) -> None:
        """Build the dialog; ``danger`` switches the confirm button to red."""
        super().__init__(master, fg_color=Theme.BG)
        self._on_confirm = on_confirm
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        color, hover = (Theme.RED, Theme.RED_HOVER) if danger else (Theme.ACCENT, Theme.ACCENT_HOVER)

        card = make_card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text=title, font=font(18, "bold"), text_color=Theme.TEXT, anchor="w").pack(
            fill="x", padx=22, pady=(20, 6))
        ctk.CTkLabel(card, text=message, font=font(13), text_color=Theme.MUTED, anchor="w", justify="left",
                     wraplength=380).pack(fill="x", padx=22)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(22, 20))
        primary_button(row, confirm_text, self._confirm, color=color, hover=hover, width=130).pack(side="right")
        ghost_button(row, "Cancel", self.destroy, width=100).pack(side="right", padx=(0, 10))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._confirm())
        center_over(self, master)
        self.after(150, lambda: activate_modal(self))

    def _confirm(self) -> None:
        """Close the dialog and run the confirmation callback."""
        self.destroy()
        self._on_confirm()


# ---------------------------------------------------------------------------
# Reusable composite widgets
# ---------------------------------------------------------------------------
class PageHeader(ctk.CTkFrame):
    """Page title with a subtitle line and a right-aligned slot for actions."""

    def __init__(self, master: Any, title: str, subtitle: str = "") -> None:
        """Create the header; add buttons to ``self.actions``."""
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=title, font=font(30, "bold", "display"), text_color=Theme.TEXT, anchor="w").grid(
            row=0, column=0, sticky="w")
        self.subtitle = ctk.CTkLabel(self, text=subtitle, font=font(13), text_color=Theme.MUTED, anchor="w")
        self.subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.actions = ctk.CTkFrame(self, fg_color="transparent", width=0, height=0)
        self.actions.grid(row=0, column=1, rowspan=2, sticky="e")


class StatCard(ctk.CTkFrame):
    """Headline metric card: label, large value with unit, and a context line."""

    def __init__(self, master: Any, title: str, glyph: str, accent: str) -> None:
        """Create a card titled ``title`` with a tinted ``glyph`` chip in ``accent``."""
        super().__init__(master, fg_color=Theme.CARD, corner_radius=16, border_width=1, border_color=Theme.BORDER)
        self.grid_columnconfigure(0, weight=1)
        WrapLabel(self, text=title, font=font(13), text_color=Theme.MUTED).grid(
            row=0, column=0, sticky="ew", padx=(20, 8), pady=(18, 0))
        ctk.CTkLabel(self, text=glyph, width=32, height=32, corner_radius=9, fg_color=blend(accent, Theme.CARD, 0.16),
                     text_color=accent, font=font(16, "bold")).grid(row=0, column=1, sticky="e", padx=(0, 16),
                                                                     pady=(14, 0))
        value_row = ctk.CTkFrame(self, fg_color="transparent")
        value_row.grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(2, 0))
        self.value_label = ctk.CTkLabel(value_row, text="—", font=font(38, "bold", "display"), text_color=Theme.TEXT)
        self.value_label.pack(side="left")
        self.unit_label = ctk.CTkLabel(value_row, text="", font=font(15, "bold", "display"), text_color=Theme.MUTED)
        self.unit_label.pack(side="left", padx=(6, 0), pady=(12, 0))
        self.subtitle_label = WrapLabel(self, text="", font=font(12), text_color=Theme.MUTED)
        self.subtitle_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 18))

    def set_values(self, value: str, unit: str = "", subtitle: str = "", subtitle_color: str = Theme.MUTED) -> None:
        """Update the displayed value, unit and context line."""
        self.value_label.configure(text=value)
        self.unit_label.configure(text=unit)
        self.subtitle_label.configure(text=subtitle, text_color=subtitle_color)


class HighlightCard(ctk.CTkFrame):
    """Compact card for PR badges and insights, with an optional "New" chip for fresh PRs."""

    def __init__(self, master: Any, title: str, color: str) -> None:
        """Create a card labelled ``title`` with a colour-coded dot."""
        super().__init__(master, fg_color=Theme.CARD, corner_radius=14, border_width=1, border_color=Theme.BORDER)
        self.color = color
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(head, text="●", font=font(12), text_color=color, width=12).pack(side="left")
        ctk.CTkLabel(head, text=title, font=font(13), text_color=Theme.MUTED).pack(side="left", padx=(6, 0))
        value_row = ctk.CTkFrame(self, fg_color="transparent")
        value_row.pack(fill="x", padx=18, pady=(4, 0))
        self.chip = ctk.CTkLabel(value_row, text="New", height=22, corner_radius=6, fg_color=color,
                                 text_color=Theme.ON_ACCENT, font=font(11, "bold"))
        self.value_label = ctk.CTkLabel(value_row, text="—", font=font(28, "bold", "display"), text_color=Theme.TEXT)
        self.value_label.pack(side="left")
        self.unit_label = ctk.CTkLabel(value_row, text="", font=font(13, "bold", "display"), text_color=Theme.MUTED)
        self.unit_label.pack(side="left", padx=(5, 0), pady=(8, 0))
        self.meta_label = WrapLabel(self, text="", font=font(12), text_color=Theme.MUTED)
        self.meta_label.pack(fill="x", padx=18, pady=(0, 14))

    def set_content(self, value: str, unit: str, meta: str, *, chip: bool = False) -> None:
        """Update the card; ``chip`` shows the "New" tag and an accent border."""
        self.value_label.configure(text=value)
        self.unit_label.configure(text=unit)
        self.meta_label.configure(text=meta)
        if chip:
            self.chip.pack(side="right", pady=(6, 0), before=self.value_label)
            self.configure(border_color=self.color)
        else:
            self.chip.pack_forget()
            self.configure(border_color=Theme.BORDER)


class ChartCard(ctk.CTkFrame):
    """Rounded card hosting an embedded, dark-themed Matplotlib figure."""

    def __init__(self, master: Any, title: str, subtitle: str) -> None:
        """Create the card header and an empty figure canvas."""
        super().__init__(master, fg_color=Theme.CARD, corner_radius=16, border_width=1, border_color=Theme.BORDER)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        ctk.CTkLabel(header, text=title, font=font(16, "bold"), text_color=Theme.TEXT, anchor="w").pack(fill="x")
        WrapLabel(header, text=subtitle, font=font(12), text_color=Theme.MUTED).pack(fill="x")
        self.figure = Figure(figsize=(5.4, 3.0), dpi=100, facecolor=Theme.CARD, layout="constrained")
        self.ax: Axes = self.figure.add_subplot(111)
        self.canvas = DarkFigureCanvas(self.figure, master=self)
        widget = self.canvas.get_tk_widget()
        widget.configure(background=Theme.CARD, highlightthickness=0, borderwidth=0)
        widget.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 14))

    def reset_axes(self) -> Axes:
        """Clear the axes, re-apply the theme and return them."""
        self.ax.clear()
        style_axes(self.ax)
        return self.ax

    def show_empty(self, message: str) -> None:
        """Replace the chart with a centred hint message."""
        ax = self.ax
        ax.clear()
        ax.set_facecolor(Theme.CARD)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes, color=Theme.MUTED,
                fontsize=11, linespacing=1.7)
        self.render()

    def render(self) -> None:
        """Schedule a redraw of the canvas."""
        self.canvas.draw_idle()


class SessionForm(ctk.CTkFrame):
    """Reusable input form for creating or editing a training session."""

    _LAYOUT: tuple[tuple[str, str, str, int, int, int, Optional[str]], ...] = (
        ("session_date", "Session date", "YYYY-MM-DD", 0, 0, 3, None),
        ("bodyweight", "Bodyweight (kg)", "e.g. 83.5", 0, 3, 3, Theme.VIOLET),
        ("squat", "Squat (kg)", "Best single", 1, 0, 2, Theme.LIFT_COLORS["squat"]),
        ("bench", "Bench press (kg)", "Best single", 1, 2, 2, Theme.LIFT_COLORS["bench"]),
        ("deadlift", "Deadlift (kg)", "Best single", 1, 4, 2, Theme.LIFT_COLORS["deadlift"]),
    )

    def __init__(
        self,
        master: Any,
        *,
        on_change: Optional[Callable[[], None]] = None,
        on_submit: Optional[Callable[[], None]] = None,
    ) -> None:
        """Build the fields; ``on_change`` fires on every keystroke, ``on_submit`` on Enter."""
        super().__init__(master, fg_color="transparent")
        self._on_change = on_change
        self._on_submit = on_submit
        self.entries: dict[str, ctk.CTkEntry] = {}
        self._default_placeholders: dict[str, str] = {}
        for column in range(6):
            self.grid_columnconfigure(column, weight=1, uniform="session_form")
        for name, label, placeholder, row, column, span, dot in self._LAYOUT:
            self._build_field(name, label, placeholder, row, column, span, dot)

        notes_box = ctk.CTkFrame(self, fg_color="transparent")
        notes_box.grid(row=2, column=0, columnspan=6, sticky="ew", padx=6, pady=(8, 4))
        self._field_label(notes_box, "Notes (optional)", None).pack(anchor="w", pady=(0, 6))
        self.notes = ctk.CTkTextbox(notes_box, height=70, corner_radius=10, border_width=1,
                                    border_color=Theme.BORDER, fg_color=Theme.INPUT, text_color=Theme.TEXT,
                                    font=font(13), wrap="word")
        self.notes.pack(fill="x")
        self.notes.bind("<KeyRelease>", lambda _event: self._reset_border("notes"))
        self._set_entry("session_date", date.today().isoformat())

    @staticmethod
    def _field_label(master: Any, text: str, dot: Optional[str]) -> ctk.CTkFrame:
        """Return a label row with an optional colour-coded dot."""
        row = ctk.CTkFrame(master, fg_color="transparent")
        if dot:
            ctk.CTkLabel(row, text="●", font=font(11), text_color=dot, width=10).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(row, text=text, font=font(13), text_color=Theme.MUTED).pack(side="left")
        return row

    def _build_field(self, name: str, label: str, placeholder: str, row: int, column: int, span: int,
                     dot: Optional[str]) -> None:
        """Create one labelled entry and register it under ``name``."""
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=row, column=column, columnspan=span, sticky="ew", padx=6, pady=8)
        box.grid_columnconfigure(0, weight=1)
        self._field_label(box, label, dot).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        entry = styled_entry(box, placeholder, width=110)
        entry.grid(row=1, column=0, sticky="ew")
        entry.bind("<KeyRelease>", lambda _event, field=name: self._handle_change(field))
        entry.bind("<Return>", lambda _event: self._submit())
        if name == "session_date":
            ghost_button(box, "Today", self.set_today, width=72).grid(row=1, column=1, padx=(8, 0))
        self.entries[name] = entry
        self._default_placeholders[name] = placeholder

    # -- state --------------------------------------------------------------
    def get_raw(self) -> dict[str, str]:
        """Return the current raw text of every field, keyed by database field name."""
        data = {name: entry.get().strip() for name, entry in self.entries.items()}
        data["notes"] = self.notes.get("1.0", "end-1c").strip()
        return data

    def _set_entry(self, name: str, value: str) -> None:
        """Replace the text of entry ``name`` with ``value``."""
        entry = self.entries[name]
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)

    def set_today(self) -> None:
        """Fill the date field with today's date."""
        self._set_entry("session_date", date.today().isoformat())
        self._handle_change("session_date")

    def set_field(self, name: str, value: float) -> None:
        """Set a numeric field programmatically (used by the 1RM estimator)."""
        self._set_entry(name, fmt_plain(value))
        self._handle_change(name)

    def load_record(self, record: db.SessionRecord) -> None:
        """Populate every field from an existing session."""
        self._set_entry("session_date", record.session_date.isoformat())
        for name in ("bodyweight", *an.LIFTS):
            self._set_entry(name, fmt_plain(getattr(record, name)))
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", record.notes)
        self.clear_errors()

    def clear(self) -> None:
        """Empty every field and reset the date to today."""
        for name in ("bodyweight", *an.LIFTS):
            self._set_entry(name, "")
        self.notes.delete("1.0", "end")
        self.clear_errors()
        self.set_today()

    def set_placeholders(self, last: Optional[db.SessionRecord]) -> None:
        """Show the previous session's values as hints inside empty fields."""
        for name in ("bodyweight", *an.LIFTS):
            hint = f"Last: {fmt_plain(getattr(last, name))}" if last else self._default_placeholders[name]
            self.entries[name].configure(placeholder_text=hint)

    # -- validation feedback -------------------------------------------------
    def mark_errors(self, errors: Mapping[str, str]) -> None:
        """Outline every invalid field in red and focus the first one."""
        first: Optional[ctk.CTkEntry] = None
        for name in errors:
            if name in self.entries:
                self.entries[name].configure(border_color=Theme.RED, border_width=2)
                first = first or self.entries[name]
            elif name == "notes":
                self.notes.configure(border_color=Theme.RED, border_width=2)
        if first is not None:
            first.focus_set()

    def clear_errors(self) -> None:
        """Remove every red error outline."""
        for name in (*self.entries, "notes"):
            self._reset_border(name)

    def _reset_border(self, name: str) -> None:
        """Restore the default border of field ``name``."""
        widget = self.notes if name == "notes" else self.entries[name]
        widget.configure(border_color=Theme.BORDER, border_width=1)

    def _handle_change(self, name: str) -> None:
        """Clear the error state of ``name`` and notify listeners."""
        self._reset_border(name)
        if self._on_change:
            self._on_change()

    def _submit(self) -> None:
        """Invoke the submit callback (bound to Enter)."""
        if self._on_submit:
            self._on_submit()


class EditSessionDialog(ctk.CTkToplevel):
    """Modal dialog for editing an existing session."""

    def __init__(self, app: "ApexSBDApp", record: db.SessionRecord) -> None:
        """Open the editor pre-filled with ``record``."""
        super().__init__(app, fg_color=Theme.BG)
        self.app = app
        self.record = record
        self.title(f"Edit session #{record.id}")
        self.resizable(False, False)
        self.transient(app)

        card = make_card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        card_header(card, f"Edit session #{record.id}",
                    f"Logged for {fmt_date(record.session_date)}. Changes recalculate every metric and PR.")
        self.form = SessionForm(card, on_submit=self._save)
        self.form.pack(fill="x", padx=16, pady=(10, 0))
        self.form.load_record(record)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(14, 20))
        primary_button(row, "Save changes", self._save, width=150).pack(side="right")
        ghost_button(row, "Cancel", self.destroy, width=100).pack(side="right", padx=(0, 10))

        self.minsize(640, 0)
        self.bind("<Escape>", lambda _event: self.destroy())
        center_over(self, app)
        self.after(150, lambda: activate_modal(self))

    def _save(self) -> None:
        """Validate and persist the edit; close only on success."""
        if self.app.save_session(self.form, session_id=self.record.id):
            self.destroy()


class NavButton(ctk.CTkFrame):
    """Sidebar navigation entry with an accent indicator bar."""

    def __init__(self, master: Any, glyph: str, text: str, command: Callable[[], None]) -> None:
        """Create the entry showing ``glyph`` and ``text``."""
        super().__init__(master, fg_color="transparent")
        self.indicator = ctk.CTkFrame(self, width=4, height=26, corner_radius=2, fg_color=Theme.SIDEBAR)
        self.indicator.pack(side="left", padx=(0, 10))
        self.button = ctk.CTkButton(
            self, text=f"{glyph}    {text}", anchor="w", height=44, corner_radius=10, fg_color="transparent",
            hover_color=Theme.CARD, text_color=Theme.MUTED, font=font(14, "bold"), command=command,
        )
        self.button.pack(side="left", fill="x", expand=True, padx=(0, 14))

    def set_active(self, active: bool) -> None:
        """Highlight the entry when it represents the visible page."""
        self.indicator.configure(fg_color=Theme.ACCENT if active else Theme.SIDEBAR)
        self.button.configure(fg_color=Theme.CARD if active else "transparent",
                              text_color=Theme.TEXT if active else Theme.MUTED)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
class DashboardView(ctk.CTkFrame):
    """Analytics overview: headline metrics, two charts and PR highlights."""

    def __init__(self, master: Any, app: "ApexSBDApp") -> None:
        """Lay out the stat cards, chart cards and PR badges."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.header = PageHeader(self, "Dashboard")
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        primary_button(self.header.actions, "+  Log session", lambda: app.show_view("log"), width=150,
                       height=42).pack(side="right")

        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.card_total = StatCard(stats, "Current SBD total", "Σ", Theme.ACCENT)
        self.card_pr = StatCard(stats, "All-time PR total", "★", Theme.ORANGE)
        self.card_dots = StatCard(stats, "DOTS score", "◆", Theme.CYAN)
        self.card_ratio = StatCard(stats, "Strength ratio", "×", Theme.VIOLET)
        self._grid_row(stats, [self.card_total, self.card_pr, self.card_dots, self.card_ratio])

        charts = ctk.CTkFrame(self, fg_color="transparent")
        charts.grid(row=2, column=0, sticky="nsew", pady=(0, 16))
        charts.grid_rowconfigure(0, weight=1)
        charts.grid_columnconfigure(0, weight=5, uniform="charts")
        charts.grid_columnconfigure(1, weight=4, uniform="charts")
        self.progress_chart = ChartCard(charts, "Lift progression", "Best single per session, in kilograms")
        self.progress_chart.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.bw_chart = ChartCard(charts, "Bodyweight vs. total",
                                  "Trendline plus an equal-DOTS reference curve")
        self.bw_chart.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        highlights = ctk.CTkFrame(self, fg_color="transparent")
        highlights.grid(row=3, column=0, sticky="ew")
        self.badges: dict[str, HighlightCard] = {
            key: HighlightCard(highlights, f"{LIFT_LABELS[key]} PR", Theme.LIFT_COLORS[key]) for key in an.PR_CATEGORIES
        }
        self.trend_card = HighlightCard(highlights, "Total trend", Theme.TEXT)
        self._grid_row(highlights, [*self.badges.values(), self.trend_card])

    @staticmethod
    def _grid_row(parent: ctk.CTkFrame, widgets: Sequence[ctk.CTkFrame]) -> None:
        """Place ``widgets`` in equal-width columns separated by 16 px gutters."""
        last = len(widgets) - 1
        for index, widget in enumerate(widgets):
            parent.grid_columnconfigure(index, weight=1, uniform=str(id(parent)))
            widget.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0 if index == last else 8))

    # -- refresh --------------------------------------------------------------
    def refresh(self) -> None:
        """Recompute every widget from the application's current analytics state."""
        metrics = self.app.metrics
        name = self.app.lifter_name
        if not metrics:
            self._show_empty(name)
            return
        latest = metrics[-1]
        delta = latest.delta
        count = len(metrics)
        self.header.subtitle.configure(
            text=f"Welcome back, {name}. Last session on {fmt_date(latest.session.session_date)}, "
                 f"{count} session{'s' if count != 1 else ''} logged.")

        self.card_total.set_values(
            fmt_num(latest.total), "kg",
            f"{fmt_signed(delta.total)} vs. last session" if delta else "Your first logged session",
            delta_color(delta.total) if delta else Theme.MUTED)

        best = self.app.records.get("total")
        if best is not None:
            if best.session_id == latest.session.id:
                context, color = f"Set {fmt_date(best.session_date)}, latest session", Theme.ACCENT
            else:
                gap = best.value - latest.total
                context, color = f"Set {fmt_date(best.session_date)}, {fmt_num(gap)} kg above now", Theme.MUTED
            self.card_pr.set_values(fmt_num(best.value), "kg", context, color)

        dots_context = an.dots_tier(latest.dots)
        if delta:
            dots_context += f", {delta.dots:+.1f} vs. last"
        self.card_dots.set_values(f"{latest.dots:.1f}", "pts", dots_context,
                                  delta_color(delta.dots) if delta else Theme.MUTED)

        ratios = latest.ratios
        self.card_ratio.set_values(
            f"{ratios.total:.2f}", "× BW",
            f"S {ratios.squat:.2f}   B {ratios.bench:.2f}   D {ratios.deadlift:.2f}")

        for key, card in self.badges.items():
            record = self.app.records.get(key)
            if record is None:
                card.set_content("—", "", "No record yet")
                continue
            is_new = record.session_id == latest.session.id and not record.is_baseline
            meta = fmt_date(record.session_date)
            if record.improvement is not None:
                meta += f", {fmt_signed(record.improvement)}"
            card.set_content(fmt_num(record.value), "kg", meta, chip=is_new)

        trend = an.progress_trend(self.app.sessions)
        if trend is None:
            self.trend_card.set_content("—", "", "Needs sessions on two different dates")
        else:
            self.trend_card.set_content(f"{trend.kg_per_week:+.1f}", "kg/wk",
                                        f"{trend.span_days}-day fit, R² {trend.r_squared:.2f}")

        self._draw_progression(metrics)
        self._draw_bodyweight_vs_total(metrics)

    def _show_empty(self, name: str) -> None:
        """Render the zero-data state."""
        self.header.subtitle.configure(text=f"Welcome, {name}. Log your first session to unlock analytics.")
        for card in (self.card_total, self.card_pr, self.card_dots, self.card_ratio):
            card.set_values("—", "", "No sessions logged yet")
        for card in (*self.badges.values(), self.trend_card):
            card.set_content("—", "", "No record yet")
        hint = "No sessions logged yet\nOpen Log workout to add your first one"
        self.progress_chart.show_empty(hint)
        self.bw_chart.show_empty(hint)

    def _draw_progression(self, metrics: Sequence[an.SessionMetrics]) -> None:
        """Plot squat, bench and deadlift over time with soft glow lines and end labels."""
        ax = self.progress_chart.reset_axes()
        dates = [m.session.session_date for m in metrics]
        numeric_dates = mdates.date2num(dates)
        for lift in an.LIFTS:
            color = Theme.LIFT_COLORS[lift]
            values = [getattr(m.session, lift) for m in metrics]
            ax.plot(dates, values, color=color, linewidth=9, alpha=0.08, solid_capstyle="round", zorder=2)
            ax.plot(dates, values, color=color, linewidth=2.4, marker="o", markersize=6.5,
                    markerfacecolor=Theme.CARD, markeredgecolor=color, markeredgewidth=2,
                    label=LIFT_LABELS[lift], zorder=3)
            ax.annotate(fmt_num(values[-1]), xy=(numeric_dates[-1], values[-1]), xytext=(9, 0),
                        textcoords="offset points", va="center", fontsize=10, fontweight="bold", color=color)
        locator = mdates.AutoDateLocator(minticks=3, maxticks=7)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.xaxis.get_offset_text().set_color(Theme.MUTED)
        ax.margins(x=0.09, y=0.14)
        ax.set_ylabel("kg")
        ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=3, fontsize=9.5, labelcolor=Theme.MUTED,
                  handlelength=1.6, borderaxespad=0.2)
        self.progress_chart.render()

    def _draw_bodyweight_vs_total(self, metrics: Sequence[an.SessionMetrics]) -> None:
        """Scatter total against bodyweight with a regression line and an equal-DOTS curve."""
        ax = self.bw_chart.reset_axes()
        bodyweights = [m.session.bodyweight for m in metrics]
        totals = [m.total for m in metrics]
        count = len(metrics)
        low, high = min(bodyweights), max(bodyweights)
        pad = max(0.8, (high - low) * 0.25)
        x_min, x_max = low - pad, high + pad
        steps = 40
        grid_x = [x_min + (x_max - x_min) * i / steps for i in range(steps + 1)]

        latest = metrics[-1]
        iso_totals = [an.total_for_dots(latest.dots, x, self.app.sex) for x in grid_x]
        ax.plot(grid_x, iso_totals, color=Theme.ORANGE, linewidth=1.3, linestyle=(0, (1, 2.5)), alpha=0.9,
                label=f"Equal DOTS ({latest.dots:.0f})", zorder=2)

        fit = an.linear_regression(bodyweights, totals)
        if fit is not None:
            ax.plot([x_min, x_max], [fit.predict(x_min), fit.predict(x_max)], color=Theme.VIOLET, linewidth=2,
                    linestyle="--", label=f"Trend {fit.slope:+.1f} kg per kg BW (R² {fit.r_squared:.2f})", zorder=3)

        if count > 1:
            ax.plot(bodyweights, totals, color=Theme.SUBTLE, linewidth=1, alpha=0.6, zorder=2)
        cmap = LinearSegmentedColormap.from_list("apex", [Theme.CYAN, Theme.ACCENT])
        colors = [cmap(i / max(count - 1, 1)) for i in range(count)]
        ax.scatter(bodyweights, totals, s=85, c=colors, edgecolors=Theme.CARD, linewidths=1.6, zorder=4)

        if count > 1:
            first = metrics[0]
            ax.annotate(f"Start\n{fmt_date(first.session.session_date)}", xy=(bodyweights[0], totals[0]),
                        xytext=(0, -14), textcoords="offset points", ha="center", va="top", fontsize=8.5,
                        color=Theme.CYAN)
        ax.annotate(f"Latest {fmt_num(latest.total)} kg", xy=(bodyweights[-1], totals[-1]), xytext=(0, 12),
                    textcoords="offset points", ha="center", va="bottom", fontsize=9, fontweight="bold",
                    color=Theme.ACCENT)
        ax.set_xlim(x_min, x_max)
        ax.margins(y=0.2)
        ax.set_xlabel("Bodyweight (kg)")
        ax.set_ylabel("SBD total (kg)")
        ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=1, fontsize=9, labelcolor=Theme.MUTED,
                  handlelength=2.2, borderaxespad=0.2)
        self.bw_chart.render()


class LogWorkoutView(ctk.CTkFrame):
    """Session logging page with a live metric preview and a 1RM estimator."""

    def __init__(self, master: Any, app: "ApexSBDApp") -> None:
        """Lay out the form card and the right-hand helper panel."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._estimate: Optional[float] = None
        self.grid_columnconfigure(0, weight=3, uniform="log")
        self.grid_columnconfigure(1, weight=2, uniform="log")
        self.grid_rowconfigure(1, weight=1)
        PageHeader(self, "Log workout",
                   "Record the heaviest successful single for each lift, in kg. Press Enter in any field to save."
                   ).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        self._build_form_card()
        self._build_side_panel()

    def _build_form_card(self) -> None:
        """Create the session form card with save/clear actions and a recent-sessions reference."""
        card = make_card(self)
        card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        card_header(card, "New training session")
        self.form = SessionForm(card, on_change=self._update_preview, on_submit=self._save)
        self.form.pack(fill="x", padx=16, pady=(10, 0))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(14, 16))
        primary_button(buttons, "Save session", self._save, width=160, height=44).pack(side="left")
        ghost_button(buttons, "Clear form", self._clear, width=120, height=44).pack(side="left", padx=(10, 0))

        divider(card).pack(fill="x", padx=22)
        ctk.CTkLabel(card, text="Recent sessions", font=font(14, "bold"), text_color=Theme.TEXT, anchor="w").pack(
            fill="x", padx=22, pady=(12, 4))
        self.recent_table = ctk.CTkFrame(card, fg_color="transparent")
        self.recent_table.pack(fill="x", padx=22, pady=(0, 18))

    def _build_side_panel(self) -> None:
        """Create the live preview and 1RM estimator cards."""
        panel = ctk.CTkFrame(self, fg_color="transparent")
        panel.grid(row=1, column=1, sticky="nsew", padx=(10, 0))

        preview = make_card(panel)
        preview.pack(fill="x")
        card_header(preview, "Live preview")
        tiles = ctk.CTkFrame(preview, fg_color="transparent")
        tiles.pack(fill="x", padx=14, pady=(8, 14))
        tiles.grid_columnconfigure((0, 1), weight=1, uniform="preview")
        self.preview: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}
        for index, (key, caption) in enumerate(
                (("total", "SBD total"), ("dots", "DOTS"), ("ratio", "Total / bodyweight"), ("pr", "vs. PR total"))):
            tile = ctk.CTkFrame(tiles, fg_color=Theme.CARD_ALT, corner_radius=12)
            tile.grid(row=index // 2, column=index % 2, sticky="ew", padx=6, pady=5)
            ctk.CTkLabel(tile, text=caption, font=font(12), text_color=Theme.MUTED, anchor="w").pack(
                fill="x", padx=14, pady=(10, 0))
            value = ctk.CTkLabel(tile, text="—", font=font(24, "bold", "display"), text_color=Theme.TEXT, anchor="w")
            value.pack(fill="x", padx=14)
            note = WrapLabel(tile, text=" ", font=font(11), text_color=Theme.MUTED)
            note.pack(fill="x", padx=14, pady=(0, 10))
            self.preview[key] = (value, note)

        estimator = make_card(panel)
        estimator.pack(fill="both", expand=True, pady=(16, 0))
        card_header(estimator, "1RM estimator", "Turn a set of reps into an estimated max.")
        self.est_lift = segmented(estimator, ["Squat", "Bench", "Deadlift"])
        self.est_lift.set("Squat")
        self.est_lift.pack(fill="x", padx=22, pady=(12, 10))

        inputs = ctk.CTkFrame(estimator, fg_color="transparent")
        inputs.pack(fill="x", padx=22)
        inputs.grid_columnconfigure((0, 2), weight=1)
        self.est_weight = styled_entry(inputs, "Weight (kg)", width=90)
        self.est_weight.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(inputs, text="×", font=font(18, "bold"), text_color=Theme.MUTED).grid(row=0, column=1, padx=10)
        self.est_reps = styled_entry(inputs, f"Reps (1–{an.MAX_REPS_FOR_ESTIMATE})", width=90)
        self.est_reps.grid(row=0, column=2, sticky="ew")
        for entry in (self.est_weight, self.est_reps):
            entry.bind("<Return>", lambda _event: self._run_estimate())

        controls = ctk.CTkFrame(estimator, fg_color="transparent")
        controls.pack(fill="x", padx=22, pady=(10, 0))
        controls.grid_columnconfigure(0, weight=1)
        self.est_formula = option_menu(controls, ["Average", "Epley", "Brzycki", "Lombardi"],
                                       lambda _value: self._run_estimate() if self._estimate is not None else None,
                                       width=120)
        self.est_formula.grid(row=0, column=0, sticky="ew")
        primary_button(controls, "Estimate", self._run_estimate, color=Theme.CYAN, hover=Theme.CYAN_HOVER,
                       width=110).grid(row=0, column=1, padx=(10, 0))

        result = ctk.CTkFrame(estimator, fg_color="transparent")
        result.pack(fill="x", padx=22, pady=(12, 0))
        result.grid_columnconfigure(0, weight=1)
        self.est_result = ctk.CTkLabel(result, text="—", font=font(28, "bold", "display"), text_color=Theme.CYAN,
                                       anchor="w")
        self.est_result.grid(row=0, column=0, sticky="w")
        self.est_apply = ghost_button(result, "Use in form", self._apply_estimate, width=120)
        self.est_apply.configure(state="disabled")
        self.est_apply.grid(row=0, column=1, sticky="e")
        self.est_detail = ctk.CTkLabel(estimator, text="Enter a weight and rep count above.", font=font(12),
                                       text_color=Theme.MUTED, anchor="w", justify="left")
        self.est_detail.pack(fill="x", padx=22, pady=(2, 18))

    def _render_recent(self) -> None:
        """Show the three most recent sessions as a compact, colour-coded reference table."""
        for child in self.recent_table.winfo_children():
            child.destroy()
        recent = list(reversed(self.app.metrics[-3:]))
        if not recent:
            ctk.CTkLabel(self.recent_table, text="Nothing logged yet. Your first session will appear here.",
                         font=font(12), text_color=Theme.MUTED, anchor="w").grid(row=0, column=0, sticky="w")
            return
        columns = (("Date", Theme.MUTED, "w"), ("BW", Theme.VIOLET, "e"), ("Squat", Theme.ACCENT, "e"),
                   ("Bench", Theme.CYAN, "e"), ("Deadlift", Theme.ORANGE, "e"), ("Total", Theme.TEXT, "e"))
        for col, (heading, _color, anchor) in enumerate(columns):
            self.recent_table.grid_columnconfigure(col, weight=2 if col == 0 else 1, uniform="recent")
            ctk.CTkLabel(self.recent_table, text=heading, font=font(12), text_color=Theme.SUBTLE, anchor=anchor).grid(
                row=0, column=col, sticky="ew", pady=(0, 4))
        for row, metrics in enumerate(recent, start=1):
            session = metrics.session
            values = (fmt_date(session.session_date), fmt_num(session.bodyweight), fmt_num(session.squat),
                      fmt_num(session.bench), fmt_num(session.deadlift), fmt_num(metrics.total))
            for col, (text, (_heading, color, anchor)) in enumerate(zip(values, columns)):
                weight = "bold" if col == len(values) - 1 else "normal"
                ctk.CTkLabel(self.recent_table, text=text, font=font(13, weight, "display" if col else "body"),
                             text_color=Theme.TEXT if col == 0 else color, anchor=anchor).grid(
                    row=row, column=col, sticky="ew", pady=1)

    # -- behaviour ------------------------------------------------------------
    def refresh(self) -> None:
        """Update placeholder hints and the preview after data changes."""
        self.form.set_placeholders(self.app.sessions[-1] if self.app.sessions else None)
        self._render_recent()
        self._update_preview()

    def _set_preview(self, key: str, value: str, note: str, color: str = Theme.TEXT) -> None:
        """Write one preview tile."""
        value_label, note_label = self.preview[key]
        value_label.configure(text=value, text_color=color)
        note_label.configure(text=note)

    def _update_preview(self) -> None:
        """Recalculate the live preview from the raw form values (never raises)."""
        raw = self.form.get_raw()
        lifts = [safe_positive_float(raw[name]) for name in an.LIFTS]
        bodyweight = safe_positive_float(raw["bodyweight"])
        total = round(sum(v for v in lifts if v is not None), 2) if all(v is not None for v in lifts) else None
        low, high = db.BODYWEIGHT_LIMITS_KG
        bodyweight_ok = bodyweight is not None and low <= bodyweight <= high

        if total is None:
            self._set_preview("total", "—", "Enter all three lifts")
        else:
            self._set_preview("total", fmt_kg(total), "Squat + bench + deadlift")

        if total is not None and bodyweight_ok:
            try:
                dots = an.dots_score(total, bodyweight, self.app.sex)  # type: ignore[arg-type]
                self._set_preview("dots", f"{dots:.1f}", an.dots_tier(dots), Theme.CYAN)
                self._set_preview("ratio", f"{total / bodyweight:.2f}×", f"At {fmt_kg(bodyweight)}")  # type: ignore[operator]
            except ValueError:
                self._set_preview("dots", "—", "Check the values")
                self._set_preview("ratio", "—", "Check the values")
        else:
            hint = "Needs a valid bodyweight" if not bodyweight_ok else "Needs all three lifts"
            self._set_preview("dots", "—", hint)
            self._set_preview("ratio", "—", hint)

        best = self.app.records.get("total")
        if total is None:
            self._set_preview("pr", "—", f"Current PR {fmt_kg(best.value)}" if best else "No PR on record yet")
        elif best is None:
            self._set_preview("pr", "First", "This will set your first PR", Theme.ACCENT)
        else:
            gap = total - best.value
            note = "This would be a new PR" if gap > 0.005 else f"Current PR {fmt_kg(best.value)}"
            self._set_preview("pr", fmt_signed(gap), note, delta_color(gap))

    def _save(self) -> None:
        """Validate and store the session, then reset the form."""
        if self.app.save_session(self.form):
            self.form.clear()
            self._update_preview()

    def _clear(self) -> None:
        """Reset the form to an empty state."""
        self.form.clear()
        self._update_preview()

    def _run_estimate(self) -> None:
        """Estimate a 1RM from the estimator inputs and display the breakdown."""
        try:
            weight = db.parse_lift(self.est_weight.get(), "Weight")
            reps = parse_reps(self.est_reps.get())
            estimates = an.estimate_1rm_all(weight, reps)
        except ValueError as exc:
            self.app.notify(str(exc), "error", title="Can't estimate a 1RM yet")
            return
        value = estimates[self.est_formula.get().lower()]
        self._estimate = an.round_to_increment(value, 2.5, mode="down")
        self.est_result.configure(text=f"≈ {fmt_num(value, 1)} kg")
        self.est_detail.configure(
            text=f"Epley {fmt_num(estimates['epley'], 1)}, Brzycki {fmt_num(estimates['brzycki'], 1)}, "
                 f"Lombardi {fmt_num(estimates['lombardi'], 1)} kg\n"
                 f"Loadable with 2.5 kg jumps: {fmt_kg(self._estimate)}")
        self.est_apply.configure(state="normal")

    def _apply_estimate(self) -> None:
        """Copy the loadable estimate into the selected lift field of the form."""
        if self._estimate is None:
            return
        lift = self.est_lift.get().lower()
        self.form.set_field(lift, self._estimate)
        self.app.notify(f"{LIFT_LABELS[lift]} set to {fmt_kg(self._estimate)} in the form.", "info",
                        title="Estimate applied")


class HistoryView(ctk.CTkFrame):
    """Searchable, filterable, sortable session table with edit, delete and CSV export."""

    COLUMNS: tuple[tuple[str, str, int, str], ...] = (
        ("id", "#", 44, "center"),
        ("date", "Date", 100, "center"),
        ("bodyweight", "BW", 64, "e"),
        ("squat", "Squat", 70, "e"),
        ("bench", "Bench", 70, "e"),
        ("deadlift", "Deadlift", 76, "e"),
        ("total", "Total", 78, "e"),
        ("dots", "DOTS", 66, "e"),
        ("ratio", "Total/BW", 76, "e"),
        ("delta", "Δ Total", 72, "e"),
        ("prs", "PRs", 72, "center"),
        ("notes", "Notes", 220, "w"),
    )
    FILTERS: tuple[str, ...] = ("All time", "Last 30 days", "Last 90 days", "Last 365 days", "PR sessions only")
    EXPORT_HEADERS: tuple[str, ...] = (
        "ID", "Date", "Bodyweight (kg)", "Squat (kg)", "Bench (kg)", "Deadlift (kg)", "Total (kg)", "DOTS",
        "Squat/BW", "Bench/BW", "Deadlift/BW", "Total/BW", "Delta Total (kg)", "PR Categories", "Notes",
    )

    def __init__(self, master: Any, app: "ApexSBDApp") -> None:
        """Build the toolbar, the dark Treeview and the summary footer."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.visible: list[an.SessionMetrics] = []
        self._sort_column = "date"
        self._sort_descending = True
        self._tree_width = 0
        self._resize_job: Optional[str] = None
        self._row_font = tkfont.Font(family=_FONT_FAMILIES.get("body", "TkDefaultFont"), size=10)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        PageHeader(self, "History", "Every logged session. Click a column to sort, double-click a row to edit.").grid(
            row=0, column=0, sticky="ew", pady=(0, 18))

        toolbar = make_card(self, corner_radius=14)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        toolbar.grid_columnconfigure(2, weight=1)
        self.search = styled_entry(toolbar, "Search by date or notes", width=260)
        self.search.grid(row=0, column=0, padx=(14, 8), pady=12)
        self.search.bind("<KeyRelease>", lambda _event: self.refresh())
        self.search.bind("<Escape>", lambda _event: self._clear_search())
        self.filter_menu = option_menu(toolbar, self.FILTERS, lambda _value: self.refresh(), width=170)
        self.filter_menu.grid(row=0, column=1, padx=(0, 8), pady=12)
        ghost_button(toolbar, "Edit", self._edit_selected, text_color=Theme.CYAN, width=90).grid(
            row=0, column=3, padx=(8, 0), pady=12)
        ghost_button(toolbar, "Delete", self._delete_selected, text_color=Theme.RED, width=90).grid(
            row=0, column=4, padx=(8, 0), pady=12)
        primary_button(toolbar, "Export CSV", self._export_csv, width=130).grid(row=0, column=5, padx=(8, 14),
                                                                                 pady=12)

        table_card = make_card(self)
        table_card.grid(row=2, column=0, sticky="nsew")
        table_card.grid_columnconfigure(0, weight=1)
        table_card.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table_card, columns=[c[0] for c in self.COLUMNS], show="headings",
                                 style="Apex.Treeview", selectmode="extended")
        for key, heading, width, anchor in self.COLUMNS:
            self.tree.heading(key, text=heading, anchor=anchor, command=lambda column=key: self._sort_by(column))
            self.tree.column(key, width=width, minwidth=40, anchor=anchor, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(14, 0), pady=14)
        scrollbar = ctk.CTkScrollbar(table_card, command=self.tree.yview, button_color=Theme.BORDER,
                                     button_hover_color=Theme.SUBTLE)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 8), pady=14)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure("odd", background=Theme.CARD)
        self.tree.tag_configure("even", background=blend(Theme.TEXT, Theme.CARD, 0.025))
        self.tree.tag_configure("pr", foreground=blend(Theme.ACCENT, Theme.TEXT, 0.55))
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Delete>", lambda _event: self._delete_selected())
        self.tree.bind("<Return>", lambda _event: self._edit_selected())
        self.tree.bind("<Configure>", self._on_tree_resize)

        self.footer = ctk.CTkLabel(self, text="", font=font(12), text_color=Theme.MUTED, anchor="w")
        self.footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))

    # -- data -------------------------------------------------------------------
    def _sort_key(self, metrics: an.SessionMetrics) -> Any:
        """Return the sort key for the active column."""
        session = metrics.session
        keys: dict[str, Callable[[], Any]] = {
            "id": lambda: session.id,
            "date": lambda: (session.session_date, session.id),
            "bodyweight": lambda: session.bodyweight,
            "squat": lambda: session.squat,
            "bench": lambda: session.bench,
            "deadlift": lambda: session.deadlift,
            "total": lambda: metrics.total,
            "dots": lambda: metrics.dots,
            "ratio": lambda: metrics.ratios.total,
            "delta": lambda: metrics.delta.total if metrics.delta else float("-inf"),
            "prs": lambda: len(metrics.pr_categories),
            "notes": lambda: session.notes.lower(),
        }
        return keys[self._sort_column]()

    def _matches(self, metrics: an.SessionMetrics, query: str, choice: str, today: date) -> bool:
        """Return ``True`` if a session passes the search text and filter choice."""
        session = metrics.session
        if choice.startswith("Last "):
            days = int(choice.split()[1])
            if (today - session.session_date).days > days:
                return False
        elif choice == "PR sessions only" and not metrics.is_pr_session:
            return False
        if query:
            haystack = f"{session.session_date.isoformat()} {fmt_date(session.session_date)} {session.notes}".lower()
            return query in haystack
        return True

    def refresh(self) -> None:
        """Re-filter, re-sort and re-render the table and its footer."""
        query = self.search.get().strip().lower()
        choice = self.filter_menu.get()
        today = date.today()
        self.visible = [m for m in self.app.metrics if self._matches(m, query, choice, today)]
        self.visible.sort(key=self._sort_key, reverse=self._sort_descending)

        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for index, metrics in enumerate(self.visible):
            session = metrics.session
            tags = ["even" if index % 2 == 0 else "odd"]
            if metrics.is_pr_session:
                tags.append("pr")
            first_line = session.notes.splitlines()[0] if session.notes else ""
            notes_px = max(40, int(self.tree.column("notes", "width")) - 18)
            self.tree.insert("", "end", iid=str(session.id), tags=tags, values=(
                session.id,
                session.session_date.isoformat(),
                fmt_num(session.bodyweight),
                fmt_num(session.squat),
                fmt_num(session.bench),
                fmt_num(session.deadlift),
                fmt_num(metrics.total),
                f"{metrics.dots:.1f}",
                f"{metrics.ratios.total:.2f}×",
                fmt_signed(metrics.delta.total, "") if metrics.delta else "—",
                " ".join(LIFT_INITIALS[c] for c in metrics.pr_categories) or "",
                self._fit_text(first_line, notes_px),
            ))
        keep = [iid for iid in selected if self.tree.exists(iid)]
        if keep:
            self.tree.selection_set(keep)
        self._update_headings()
        self._update_footer()

    def _fit_text(self, text: str, max_px: int) -> str:
        """Trim ``text`` with an ellipsis so it fits inside ``max_px`` pixels of the row font."""
        if self._row_font.measure(text) <= max_px:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self._row_font.measure(text[:middle].rstrip() + "…") <= max_px:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "…"

    def _on_tree_resize(self, event: tk.Event) -> None:
        """Re-fit the notes column (debounced) when the table width changes."""
        if event.width == self._tree_width:
            return
        self._tree_width = event.width
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._finish_resize)

    def _finish_resize(self) -> None:
        """Run the deferred refresh scheduled by :meth:`_on_tree_resize`."""
        self._resize_job = None
        self.refresh()

    def _update_headings(self) -> None:
        """Show a sort arrow on the active column heading."""
        for key, heading, _width, _anchor in self.COLUMNS:
            arrow = (" ▼" if self._sort_descending else " ▲") if key == self._sort_column else ""
            self.tree.heading(key, text=heading + arrow)

    def _update_footer(self) -> None:
        """Summarise the visible rows with descriptive statistics."""
        total_count = len(self.app.metrics)
        totals = an.summary_statistics([m.total for m in self.visible])
        dots = an.summary_statistics([m.dots for m in self.visible])
        if totals is None or dots is None:
            self.footer.configure(text=f"No sessions match the current search and filter ({total_count} in total).")
            return
        self.footer.configure(
            text=f"Showing {totals.count} of {total_count} sessions.   Average total {fmt_kg(totals.mean)} "
                 f"(σ {fmt_num(totals.stdev)}), best {fmt_kg(totals.maximum)}, average DOTS {dots.mean:.1f}.   "
                 f"PR columns: S squat, B bench, D deadlift, T total.")

    def _sort_by(self, column: str) -> None:
        """Sort by ``column``; clicking the active column toggles direction."""
        if column == self._sort_column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = column != "notes"
        self.refresh()

    def _clear_search(self) -> None:
        """Empty the search box and show every row again."""
        self.search.delete(0, "end")
        self.refresh()

    # -- actions ----------------------------------------------------------------
    def _selected_ids(self) -> list[int]:
        """Return the ids of the selected rows."""
        return [int(iid) for iid in self.tree.selection()]

    def _on_double_click(self, event: tk.Event) -> None:
        """Open the editor when a data row (not a heading) is double-clicked."""
        if self.tree.identify_region(event.x, event.y) == "cell":
            self._edit_selected()

    def _edit_selected(self) -> None:
        """Open the edit dialog for exactly one selected session."""
        ids = self._selected_ids()
        if not ids:
            self.app.notify("Select a session in the table first.", "warning", title="Nothing selected")
            return
        if len(ids) > 1:
            self.app.notify("Select a single session to edit.", "warning", title="Too many rows selected")
            return
        try:
            record = self.app.db.get_session(ids[0])
        except db.DatabaseError as exc:
            self.app.notify(str(exc), "error", title="Couldn't load session")
            return
        if record is None:
            self.app.notify("That session no longer exists.", "error", title="Session missing")
            self.app.refresh_all()
            return
        EditSessionDialog(self.app, record)

    def _delete_selected(self) -> None:
        """Ask for confirmation, then delete the selected session(s)."""
        ids = self._selected_ids()
        if not ids:
            self.app.notify("Select one or more sessions to delete.", "warning", title="Nothing selected")
            return
        if len(ids) == 1:
            record = next((m.session for m in self.visible if m.session.id == ids[0]), None)
            label = f"the session from {fmt_date(record.session_date)}" if record else f"session #{ids[0]}"
            title, message = "Delete session?", f"This permanently removes {label}. PRs and trends will be recalculated."
        else:
            title = f"Delete {len(ids)} sessions?"
            message = "This permanently removes the selected sessions. PRs and trends will be recalculated."
        ConfirmDialog(self.app, title, message, lambda: self.app.delete_sessions(ids), confirm_text="Delete")

    def _export_row(self, metrics: an.SessionMetrics) -> list[Any]:
        """Return one enriched CSV row for ``metrics``."""
        session, ratios = metrics.session, metrics.ratios
        return [
            session.id, session.session_date.isoformat(), session.bodyweight, session.squat, session.bench,
            session.deadlift, metrics.total, metrics.dots, ratios.squat, ratios.bench, ratios.deadlift, ratios.total,
            metrics.delta.total if metrics.delta else "",
            ";".join(metrics.pr_categories), session.notes,
        ]

    def _export_csv(self) -> None:
        """Export the currently visible rows, with every computed metric, to CSV."""
        if not self.visible:
            self.app.notify("The current search and filter show no sessions to export.", "warning",
                            title="Nothing to export")
            return
        path = filedialog.asksaveasfilename(
            parent=self.app, title="Export history to CSV", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"apex_sbd_history_{date.today():%Y%m%d}.csv",
        )
        if not path:
            return
        rows = [self._export_row(m) for m in sorted(self.visible, key=lambda m: (m.session.session_date, m.session.id))]
        try:
            count = db.export_rows_to_csv(path, self.EXPORT_HEADERS, rows)
        except db.DatabaseError as exc:
            self.app.notify(str(exc), "error", title="Export failed")
            return
        self.app.notify(f"Exported {count} session{'s' if count != 1 else ''} to {Path(path).name}.", "success",
                        title="History exported")


class SettingsView(ctk.CTkFrame):
    """Lifter profile, data management (import/export/backup/reset) and DOTS reference."""

    def __init__(self, master: Any, app: "ApexSBDApp") -> None:
        """Build the profile, DOTS reference and data management cards."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        PageHeader(self, "Settings & data",
                   "Manage your lifter profile, back up your training log and move data in or out.").grid(
            row=0, column=0, sticky="ew", pady=(0, 18))
        body = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=Theme.BORDER,
                                      scrollbar_button_hover_color=Theme.SUBTLE)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure((0, 1), weight=1, uniform="settings")
        self._build_profile_card(body)
        self._build_dots_card(body)
        self._build_data_card(body)

    def _build_profile_card(self, body: ctk.CTkScrollableFrame) -> None:
        """Create the lifter profile card."""
        card = make_card(body)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 16))
        card_header(card, "Lifter profile", "Your name appears on the dashboard. Sex selects the DOTS coefficients.")
        ctk.CTkLabel(card, text="Display name", font=font(13), text_color=Theme.MUTED, anchor="w").pack(
            fill="x", padx=22, pady=(16, 6))
        self.name_entry = styled_entry(card, "Your name")
        self.name_entry.pack(fill="x", padx=22)
        self.name_entry.bind("<Return>", lambda _event: self._save_profile())
        ctk.CTkLabel(card, text="Sex for DOTS scoring", font=font(13), text_color=Theme.MUTED, anchor="w").pack(
            fill="x", padx=22, pady=(16, 6))
        self.sex_toggle = segmented(card, ["Male", "Female"])
        self.sex_toggle.pack(fill="x", padx=22)
        primary_button(card, "Save profile", self._save_profile, width=150).pack(anchor="w", padx=22, pady=(20, 22))

    def _build_dots_card(self, body: ctk.CTkScrollableFrame) -> None:
        """Create the DOTS formula reference card."""
        card = make_card(body)
        card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 16))
        card_header(card, "How DOTS is calculated",
                    "DOTS compares strength across bodyweights so a lighter and a heavier lifter can be ranked fairly.")
        formula = ctk.CTkFrame(card, fg_color=Theme.CARD_ALT, corner_radius=12)
        formula.pack(fill="x", padx=22, pady=(16, 0))
        WrapLabel(formula, text="DOTS = Total × 500 ÷ P(BW)\nP(BW) = a·BW⁴ + b·BW³ + c·BW² + d·BW + e",
                  font=font(15, "bold", "display"), text_color=Theme.CYAN).pack(fill="x", padx=16, pady=(14, 6))
        self.coefficients_label = WrapLabel(formula, text="", font=font(12), text_color=Theme.MUTED)
        self.coefficients_label.pack(fill="x", padx=16, pady=(0, 14))
        tiers = ", ".join(f"{label} {bound:g}+" for bound, label in reversed(an.DOTS_TIERS[:-1]))
        WrapLabel(card, text=f"Approximate tiers used on the dashboard: {tiers}.", font=font(12),
                  text_color=Theme.MUTED).pack(fill="x", padx=22, pady=(12, 22))

    def _build_data_card(self, body: ctk.CTkScrollableFrame) -> None:
        """Create the data management card with one row per action."""
        card = make_card(body)
        card.grid(row=1, column=0, columnspan=2, sticky="nsew")
        card_header(card, "Data management", "Your log is stored locally in a single SQLite file.")
        actions = (
            ("Export all sessions", "Save every session to a CSV file for Excel, Sheets or a coach.",
             "Export CSV", self._export_all, "primary"),
            ("Import from CSV", "Add sessions from a CSV with Date, Bodyweight, Squat, Bench and Deadlift columns. "
                               "Exact duplicates are skipped.", "Import CSV", self._import_csv, "ghost"),
            ("Back up database", "Write a consistent copy of the database file somewhere safe.",
             "Back up", self._backup, "ghost"),
            ("Restore sample data", "Replace every session with the six-session demo training block.",
             "Restore demo", self._restore_sample, "warning"),
            ("Delete all sessions", "Permanently erase your training history. This cannot be undone.",
             "Delete all", self._delete_all, "danger"),
        )
        for index, (title, description, button_text, command, kind) in enumerate(actions):
            if index:
                divider(card).pack(fill="x", padx=22)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=22, pady=12)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=title, font=font(14, "bold"), text_color=Theme.TEXT, anchor="w").grid(
                row=0, column=0, sticky="w")
            WrapLabel(row, text=description, font=font(12), text_color=Theme.MUTED).grid(row=1, column=0, sticky="ew")
            if kind == "primary":
                button = primary_button(row, button_text, command, width=140)
            elif kind == "danger":
                button = ghost_button(row, button_text, command, text_color=Theme.RED,
                                      border_color=blend(Theme.RED, Theme.CARD, 0.5), width=140)
            elif kind == "warning":
                button = ghost_button(row, button_text, command, text_color=Theme.ORANGE, width=140)
            else:
                button = ghost_button(row, button_text, command, width=140)
            button.grid(row=0, column=1, rowspan=2, padx=(16, 0))
        self.storage_label = WrapLabel(card, text="", font=font(12), text_color=Theme.SUBTLE)
        self.storage_label.pack(fill="x", padx=22, pady=(4, 20))

    # -- behaviour ------------------------------------------------------------
    def refresh(self) -> None:
        """Sync the profile fields, coefficient readout and storage info with the database."""
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, self.app.lifter_name)
        self.sex_toggle.set(self.app.sex.capitalize())
        a, b, c, d, e = an.DOTS_COEFFICIENTS[self.app.sex]
        low, high = an.DOTS_BODYWEIGHT_BOUNDS[self.app.sex]
        self.coefficients_label.configure(
            text=f"{self.app.sex.capitalize()} coefficients\n"
                 f"a = {a:.10f}   b = {b:.10f}\nc = {c:.10f}   d = {d:.7f}   e = {e:.5f}\n"
                 f"Bodyweight is clamped to {low:g}–{high:g} kg before the polynomial is applied.")
        self.storage_label.configure(text=f"Database file: {self.app.db.path}\n"
                                          f"{len(self.app.sessions)} session(s) stored.")

    def _save_profile(self) -> None:
        """Validate and persist the lifter name and sex."""
        try:
            name = db.validate_lifter_name(self.name_entry.get())
            sex = db.validate_sex(self.sex_toggle.get())
        except ValueError as exc:
            self.name_entry.configure(border_color=Theme.RED, border_width=2)
            self.app.notify(str(exc), "error", title="Profile not saved")
            return
        self.name_entry.configure(border_color=Theme.BORDER, border_width=1)
        try:
            self.app.db.set_lifter_name(name)
            self.app.db.set_lifter_sex(sex)
        except (db.DatabaseError, ValueError) as exc:
            self.app.notify(str(exc), "error", title="Profile not saved")
            return
        self.app.refresh_all()
        self.app.notify(f"DOTS now uses the {sex} coefficients.", "success", title="Profile saved")

    def _export_all(self) -> None:
        """Export every stored session to a CSV file."""
        if not self.app.sessions:
            self.app.notify("There are no sessions to export yet.", "warning", title="Nothing to export")
            return
        path = filedialog.asksaveasfilename(
            parent=self.app, title="Export all sessions", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"apex_sbd_all_sessions_{date.today():%Y%m%d}.csv",
        )
        if not path:
            return
        try:
            count = self.app.db.export_sessions_to_csv(path)
        except db.DatabaseError as exc:
            self.app.notify(str(exc), "error", title="Export failed")
            return
        self.app.notify(f"Exported {count} session{'s' if count != 1 else ''} to {Path(path).name}.", "success",
                        title="Sessions exported")

    def _import_csv(self) -> None:
        """Import sessions from a CSV file and report what happened."""
        path = filedialog.askopenfilename(parent=self.app, title="Import sessions from CSV",
                                          filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            report = self.app.db.import_sessions_from_csv(path)
        except db.DatabaseError as exc:
            self.app.notify(str(exc), "error", title="Import failed")
            return
        self.app.refresh_all()
        kind = "success" if report.imported else "warning"
        self.app.notify(
            f"{report.imported} imported, {report.duplicates} duplicate(s) skipped, {report.rejected} invalid row(s).",
            kind, title="Import finished")
        if report.errors:
            preview = "\n".join(report.errors[:3]) + ("\n…" if len(report.errors) > 3 else "")
            self.app.notify(preview, "warning", title="Some rows were rejected", duration_ms=9000)

    def _backup(self) -> None:
        """Save a copy of the SQLite database."""
        path = filedialog.asksaveasfilename(
            parent=self.app, title="Back up database", defaultextension=".db",
            filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
            initialfile=f"apex_sbd_backup_{date.today():%Y%m%d}.db",
        )
        if not path:
            return
        try:
            target = self.app.db.backup_to(path)
        except db.DatabaseError as exc:
            self.app.notify(str(exc), "error", title="Backup failed")
            return
        self.app.notify(f"Saved to {target.name}.", "success", title="Backup complete")

    def _restore_sample(self) -> None:
        """Confirm, then replace all data with the sample block."""
        ConfirmDialog(self.app, "Restore sample data?",
                      "Every current session will be replaced by the six-session demo block. "
                      "Export or back up first if you want to keep your log.",
                      self._do_restore_sample, confirm_text="Restore demo")

    def _do_restore_sample(self) -> None:
        """Replace all sessions with the sample block."""
        try:
            count = self.app.db.reset_to_sample_data()
        except db.DatabaseError as exc:
            self.app.notify(str(exc), "error", title="Restore failed")
            return
        self.app.refresh_all()
        self.app.notify(f"Loaded {count} demo sessions.", "success", title="Sample data restored")

    def _delete_all(self) -> None:
        """Confirm, then erase every session."""
        if not self.app.sessions:
            self.app.notify("The log is already empty.", "info", title="Nothing to delete")
            return
        ConfirmDialog(self.app, "Delete all sessions?",
                      f"All {len(self.app.sessions)} sessions will be permanently erased. This cannot be undone.",
                      self._do_delete_all, confirm_text="Delete all")

    def _do_delete_all(self) -> None:
        """Erase every session."""
        try:
            removed = self.app.db.delete_all_sessions()
        except db.DatabaseError as exc:
            self.app.notify(str(exc), "error", title="Delete failed")
            return
        self.app.refresh_all()
        self.app.notify(f"Removed {removed} session{'s' if removed != 1 else ''}.", "success", title="Log cleared")


# ---------------------------------------------------------------------------
# Application shell
# ---------------------------------------------------------------------------
def configure_treeview_style(root: tk.Misc) -> None:
    """Create the dark ``Apex.Treeview`` ttk style used by the history table."""
    style = ttk.Style(root)
    style.theme_use("clam")
    scale = window_scaling(root)
    family = _FONT_FAMILIES.get("body", "TkDefaultFont")
    style.layout("Apex.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Apex.Treeview", background=Theme.CARD, fieldbackground=Theme.CARD, foreground=Theme.TEXT,
                    rowheight=int(34 * scale), borderwidth=0, relief="flat", font=(family, 10),
                    bordercolor=Theme.CARD, lightcolor=Theme.CARD, darkcolor=Theme.CARD)
    style.map("Apex.Treeview",
              background=[("selected", blend(Theme.ACCENT, Theme.CARD, 0.24))],
              foreground=[("selected", "#ffffff")])
    style.configure("Apex.Treeview.Heading", background=Theme.CARD_ALT, foreground=Theme.MUTED, relief="flat",
                    borderwidth=0, font=(family, 10, "bold"), padding=(6, 9),
                    bordercolor=Theme.CARD_ALT, lightcolor=Theme.CARD_ALT, darkcolor=Theme.CARD_ALT)
    style.map("Apex.Treeview.Heading",
              background=[("active", Theme.BORDER)], foreground=[("active", Theme.TEXT)])


class ApexSBDApp(ctk.CTk):
    """Main window: sidebar navigation, page container, shared state and CRUD orchestration."""

    NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
        ("dashboard", "▦", "Dashboard"),
        ("log", "+", "Log workout"),
        ("history", "☰", "History"),
        ("settings", "◎", "Settings & data"),
    )

    def __init__(self, database: db.Database) -> None:
        """Build the window around an open :class:`database.Database`."""
        super().__init__(fg_color=Theme.BG)
        resolve_font_families(self)
        self.db = database
        self.sessions: list[db.SessionRecord] = []
        self.metrics: list[an.SessionMetrics] = []
        self.records: dict[str, an.PersonalRecord] = {}
        self.sex: str = "male"
        self.lifter_name: str = db.DEFAULT_LIFTER_NAME
        self.current_view: Optional[str] = None

        self.title(f"{APP_NAME} – Powerlifting Progress & Analytics")
        self._size_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        configure_treeview_style(self)
        self.toasts = ToastManager(self)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.views: dict[str, Any] = {
            "dashboard": DashboardView(self.content, self),
            "log": LogWorkoutView(self.content, self),
            "history": HistoryView(self.content, self),
            "settings": SettingsView(self.content, self),
        }
        self._bind_shortcuts()
        self.refresh_all()
        self.show_view("dashboard")

    # -- layout -----------------------------------------------------------------
    def _size_window(self) -> None:
        """Size the window to fit the screen (max 1440×900 logical px) and centre it."""
        scale = window_scaling(self)
        screen_w = self.winfo_screenwidth() / scale
        screen_h = self.winfo_screenheight() / scale
        width, height = int(min(1440, screen_w * 0.94)), int(min(900, screen_h * 0.9))
        self.geometry(f"{width}x{height}")
        self.minsize(min(1180, width), min(760, height))
        x = int((self.winfo_screenwidth() - width * scale) / 2)
        y = int((self.winfo_screenheight() - height * scale) / 3)
        tk.Wm.wm_geometry(self, f"+{max(x, 0)}+{max(y, 0)}")

    def _build_sidebar(self) -> None:
        """Create the brand mark, navigation and lifter profile footer."""
        sidebar = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color=Theme.SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.pack_propagate(False)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(28, 30))
        ctk.CTkLabel(brand, text="▲", width=40, height=40, corner_radius=10, fg_color=Theme.ACCENT,
                     text_color=Theme.ON_ACCENT, font=font(18, "bold")).pack(side="left")
        words = ctk.CTkFrame(brand, fg_color="transparent")
        words.pack(side="left", padx=(12, 0))
        title_row = ctk.CTkFrame(words, fg_color="transparent")
        title_row.pack(anchor="w")
        ctk.CTkLabel(title_row, text="APEX", font=font(24, "bold", "display"), text_color=Theme.TEXT).pack(side="left")
        ctk.CTkLabel(title_row, text=" SBD", font=font(24, "bold", "display"), text_color=Theme.ACCENT).pack(side="left")
        ctk.CTkLabel(words, text="Powerlifting analytics", font=font(12), text_color=Theme.MUTED).pack(anchor="w")

        self.nav_buttons: dict[str, NavButton] = {}
        for key, glyph, label in self.NAV_ITEMS:
            button = NavButton(sidebar, glyph, label, lambda k=key: self.show_view(k))
            button.pack(fill="x", pady=3)
            self.nav_buttons[key] = button

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=16, pady=20)
        profile = make_card(footer, corner_radius=12, fg_color=Theme.CARD)
        profile.pack(fill="x")
        self.avatar = ctk.CTkLabel(profile, text="L", width=40, height=40, corner_radius=10,
                                   fg_color=blend(Theme.ACCENT, Theme.CARD, 0.18), text_color=Theme.ACCENT,
                                   font=font(15, "bold"))
        self.avatar.pack(side="left", padx=(12, 10), pady=12)
        text = ctk.CTkFrame(profile, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        self.profile_name = ctk.CTkLabel(text, text="", font=font(13, "bold"), text_color=Theme.TEXT, anchor="w")
        self.profile_name.pack(fill="x")
        self.profile_meta = ctk.CTkLabel(text, text="", font=font(11), text_color=Theme.MUTED, anchor="w")
        self.profile_meta.pack(fill="x")
        ctk.CTkLabel(footer, text=f"Version {APP_VERSION}\nCtrl+1 to 4 switches pages", font=font(11),
                     text_color=Theme.SUBTLE, anchor="w", justify="left").pack(fill="x", pady=(10, 0), padx=4)

    def _bind_shortcuts(self) -> None:
        """Register keyboard shortcuts (Ctrl+1…4 for pages, Ctrl+N to log a session)."""
        for index, (key, _glyph, _label) in enumerate(self.NAV_ITEMS, start=1):
            self.bind(f"<Control-Key-{index}>", lambda _event, k=key: self.show_view(k))
        self.bind("<Control-n>", lambda _event: self.show_view("log"))

    def show_view(self, name: str) -> None:
        """Display page ``name`` and highlight its navigation entry."""
        for view in self.views.values():
            view.grid_remove()
        self.views[name].grid(row=0, column=0, sticky="nsew")
        for key, button in self.nav_buttons.items():
            button.set_active(key == name)
        self.current_view = name

    # -- state ------------------------------------------------------------------
    def refresh_all(self) -> None:
        """Reload sessions and settings, recompute analytics and refresh every page."""
        try:
            self.sessions = self.db.list_sessions()
            self.sex = self.db.get_lifter_sex()
            self.lifter_name = self.db.get_lifter_name()
        except db.DatabaseError as exc:
            self.notify(str(exc), "error", title="Couldn't load your data")
            return
        self.metrics = an.analyze_sessions(self.sessions, self.sex)
        self.records = an.current_personal_records(self.sessions)
        for view in self.views.values():
            view.refresh()
        initials = "".join(part[0] for part in self.lifter_name.split()[:2]).upper() or "L"
        self.avatar.configure(text=initials)
        self.profile_name.configure(text=self.lifter_name)
        count = len(self.sessions)
        self.profile_meta.configure(text=f"{self.sex.capitalize()}, {count} session{'s' if count != 1 else ''}")

    def notify(self, message: str, kind: str = "info", title: Optional[str] = None, duration_ms: int = 4500) -> None:
        """Show a non-blocking toast notification."""
        self.toasts.show(message, kind, title, duration_ms)

    def save_session(self, form: SessionForm, session_id: Optional[int] = None) -> bool:
        """Validate ``form`` and create (or update ``session_id``). Returns ``True`` on success."""
        try:
            data = db.validate_session(**form.get_raw())
        except db.ValidationError as exc:
            form.mark_errors(exc.errors)
            count = len(exc.errors)
            self.notify(exc.summary(), "error", title=f"Fix {count} field{'s' if count != 1 else ''} to save",
                        duration_ms=6500)
            return False
        try:
            if self.db.is_duplicate(data, exclude_id=session_id):
                self.notify(f"A session with identical numbers already exists on {fmt_date(data.session_date)}.",
                            "warning", title="Duplicate session")
                return False
            if session_id is None:
                saved_id = self.db.add_session(data)
            else:
                self.db.update_session(session_id, data)
                saved_id = session_id
        except db.DatabaseError as exc:
            self.notify(str(exc), "error", title="Session not saved")
            return False

        self.refresh_all()
        title = "Session saved" if session_id is None else "Changes saved"
        self.notify(f"{fmt_date(data.session_date)}, total {fmt_kg(data.total)}.", "success", title=title)
        saved = next((m for m in self.metrics if m.session.id == saved_id), None)
        if session_id is None and saved is not None and saved.pr_categories:
            names = ", ".join(LIFT_LABELS[c].lower() for c in saved.pr_categories)
            self.notify(f"New best on {names}.", "success", title="★ Personal record")
        return True

    def delete_sessions(self, session_ids: Sequence[int]) -> None:
        """Delete sessions by id and refresh the UI."""
        try:
            removed = self.db.delete_sessions(session_ids)
        except db.DatabaseError as exc:
            self.notify(str(exc), "error", title="Delete failed")
            return
        self.refresh_all()
        self.notify(f"Removed {removed} session{'s' if removed != 1 else ''}. PRs recalculated.", "success",
                    title="Deleted")

    # -- lifecycle --------------------------------------------------------------
    def report_callback_exception(self, exc: type[BaseException], val: BaseException, tb: Any) -> None:
        """Log unexpected callback errors and surface them as a toast instead of crashing."""
        traceback.print_exception(exc, val, tb)
        try:
            self.notify(f"{exc.__name__}: {val}", "error", title="Unexpected error")
        except Exception:  # noqa: BLE001 - last-resort handler must never raise
            pass

    def _on_close(self) -> None:
        """Close the database and shut down cleanly."""
        self.db.close()
        self.quit()
        self.destroy()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Apex SBD – Powerlifting Progress & Analytics System")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH,
                        help=f"SQLite database file (default: {db.DEFAULT_DB_PATH.name} next to main.py)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Application entry point. Returns a process exit code."""
    args = parse_args(argv)
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")
    configure_matplotlib()
    try:
        database = db.Database(args.db)
        database.ensure_initialized()
    except db.DatabaseError as exc:
        sys.stderr.write(f"[{APP_NAME}] {exc}\n")
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_NAME, str(exc))
            root.destroy()
        except tk.TclError:
            pass
        return 1
    app = ApexSBDApp(database)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

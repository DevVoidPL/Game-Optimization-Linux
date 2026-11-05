"""Native controls and reuse of the existing EN/PL/ES Qt catalogs."""

import json

from gi.repository import Adw, Gtk
from PySide6.QtCore import QTranslator

from ..config import TRANSLATIONS_DIR


class Translator:
    def __init__(self, language):
        self.catalog = QTranslator()
        code = language if language in {"en", "pl", "es"} else "en"
        self.catalog.load(str(TRANSLATIONS_DIR / f"game_optimization_{code}.qm"))

    def __call__(self, context, text):
        return self.catalog.translate(context, text) or text


def button(label, callback):
    widget = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    widget.connect("clicked", lambda _widget: callback())
    return widget


def action(group, title, callback):
    row = Adw.ActionRow(title=title)
    row.add_suffix(button(title, callback))
    group.add(row)
    return row


def switch(group, title, value, changed):
    row = Adw.SwitchRow(title=title, active=bool(value))
    row.connect("notify::active", lambda widget, _spec: changed(widget.get_active()))
    group.add(row)
    return row


def spin(group, title, value, lower, upper, changed):
    row = Adw.SpinRow(title=title, adjustment=Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=1, page_increment=10))
    row.connect("notify::value", lambda widget, _spec: changed(int(widget.get_value())))
    group.add(row)
    return row


def choice(group, title, values, selected, changed):
    keys = list(values)
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(list(values.values())), selected=keys.index(selected) if selected in keys else 0)
    row.connect("notify::selected", lambda widget, _spec: changed(keys[widget.get_selected()]))
    group.add(row)
    return row


def report(group, title, value):
    """Read-only diagnostics, never an editable substitute for an unsafe flow."""
    view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
    view.get_buffer().set_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    expander = Adw.ExpanderRow(title=title)
    expander.add_row(view)
    group.add(expander)
    return expander


def confirm(parent, heading, body, accepted):
    dialog = Adw.MessageDialog(transient_for=parent, modal=True, heading=heading, body=body)
    dialog.add_response("cancel", parent.tr("ConfirmDialog", "Cancel"))
    dialog.add_response("apply", parent.tr("MangoHudTab", "Apply"))
    dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_close_response("cancel")
    dialog.set_default_response("cancel")
    dialog.connect("response", lambda _dialog, response: accepted() if response == "apply" else None)
    dialog.present()
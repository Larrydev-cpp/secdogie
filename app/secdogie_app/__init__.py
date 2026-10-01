"""secdogie-app: the one secdogie window.

One native dialog with the resident node on this machine: say what you want,
answer its questions, approve or deny its high-risk steps with your passphrase
(Gate 2), confirm what it wants to remember, stop it -- and set the model's API
key. ``model.DialogModel`` is the logic (headless, tested); ``window.py`` is
the tkinter view over it; ``local.LocalBackend`` is the node, wired to the
window on 127.0.0.1 with nothing to configure.
"""
from .local import ApiKeys, LocalBackend, OperatorKey, default_home
from .model import DialogError, DialogModel, Message, diff_messages

__all__ = ["ApiKeys", "DialogError", "DialogModel", "LocalBackend", "Message", "OperatorKey", "default_home",
           "diff_messages"]

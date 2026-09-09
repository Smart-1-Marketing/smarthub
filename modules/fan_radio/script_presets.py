"""Reusable spoken copy for the recording screen."""
import os
import secrets

from hub import jsonstore

DEFAULTS = [
    {"id": "default-welcome", "name": "Welcome", "script": "Welcome to {business}. We're glad you're here. Stop in and see us today."},
    {"id": "default-game-day", "name": "Game day", "script": "Make {business} part of your game day plans. Bring your friends and get ready for a great day."},
    {"id": "default-weekend", "name": "Weekend invitation", "script": "Looking for something to do this weekend? Visit {business}. We look forward to seeing you."},
    {"id": "default-thanks", "name": "Customer thank-you", "script": "Thank you for choosing {business}. We appreciate your support and look forward to welcoming you back."},
]


def _path():
    return os.path.join(jsonstore.data_dir("fan_radio"), "script_presets.json")


def library():
    return {"defaults": DEFAULTS, "custom": jsonstore.read_json(_path(), default=[])}


def save(name, script, actor):
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("Enter a script name of 1 to 80 characters.")
    if not isinstance(script, str) or not script.strip() or len(script) > 4000:
        raise ValueError("Enter a script of 1 to 4,000 characters.")
    row = {"id": secrets.token_urlsafe(12), "name": name.strip(),
           "script": script.strip(), "created_by": actor}

    def add(rows):
        if len(rows) >= 500:
            raise ValueError("The custom script library is full (500 scripts).")
        rows.append(row)
        return rows

    jsonstore.update_json(_path(), add, default=[])
    return row

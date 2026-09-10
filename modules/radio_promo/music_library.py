"""Independent saved beds: replacing a project track never overwrites a save."""
import os
import secrets
from hub import jsonstore
from . import store


def tracks():
    return jsonstore.read_json(os.path.join(store.data_dir(), "music_library.json"), default=[])


def save(name, data, details, upload):
    track_id = secrets.token_urlsafe(9)
    asset = upload(data, "smart1-radio-promo/music-library", track_id, "audio")
    track = {**details, "id": track_id, "name": name, "audio_url": asset["url"],
             "public_id": asset["public_id"], "store": asset["store"], "created_at": store.now()}
    jsonstore.update_json(os.path.join(store.data_dir(), "music_library.json"),
                          lambda rows: [track] + rows[:999], default=[])
    return track

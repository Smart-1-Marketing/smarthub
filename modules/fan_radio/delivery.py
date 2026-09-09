"""Voice settings shared by preview and final recording."""
import math
import re

TAGS = ('excited', 'curious', 'whispers', 'laughs', 'sighs', 'sarcastic', 'mischievously', 'short pause', 'long pause')


def settings(body, previous=None):
    result = dict(previous or {})
    result.update({k: body[k] for k in ('voice_id', 'name', 'energy', 'model_id') if k in body})
    result.setdefault('model_id', 'eleven_multilingual_v2')
    if result['model_id'] not in ('eleven_multilingual_v2', 'eleven_v3'):
        raise ValueError('Choose Standard or Expressive voice mode.')
    for key, default, low, high in (('speed', 1, .7, 1.2), ('stability', .5, 0, 1), ('prompt_strength', .55, 0, 1)):
        value = body.get(key, result.get(key, default))
        if value is None and key == 'prompt_strength':
            continue
        try:
            value = float(value)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f'{key.replace("_", " ").capitalize()} must be between {low} and {high}.')
        result[key] = value
    if result['model_id'] == 'eleven_v3':
        result['stability'] = min((0, .5, 1), key=lambda x: abs(x-result['stability']))
    return result


def validate_tags(text, model):
    if re.search(r'\[[^\]]+\]', text) and model != 'eleven_v3':
        raise ValueError('Choose Expressive mode to record scripts with audio tags.')


def render_options(voice):
    return {k: voice[k] for k in ('prompt_strength', 'stability', 'model_id') if voice.get(k) is not None}

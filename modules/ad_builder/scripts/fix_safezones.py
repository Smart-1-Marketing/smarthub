import json, glob

def safe_vertical_9x16(w, h):
    top = round(h * 0.14)      # 269
    bottom = round(h * 0.65)   # 1248 (content must end at/above here)
    core = bottom - top
    safe = round(w * 0.06)
    cw = w - safe * 2
    logo_y = top + round(core * 0.02)
    logo_h = round(core * 0.09)
    head_y = top + round(core * 0.16)
    head_h = round(core * 0.34)
    sup_y = top + round(core * 0.54)
    sup_h = round(core * 0.20)
    cta_h = round(core * 0.11)
    cta_y = top + core - cta_h - round(core * 0.02)
    ctaW = round(cw * 0.8)
    return {
        "canvas": {"w": w, "h": h}, "safe": safe, "background": "light",
        "logo": {"x": safe, "y": logo_y, "w": cw, "h": logo_h, "align": "center", "valign": "top"},
        "headline": {"x": safe, "y": head_y, "w": cw, "h": head_h, "size": [round(w*0.075), round(w*0.11)], "maxLines": 3, "lineHeight": 1.12, "weight": "bold", "color": "dark", "align": "center"},
        "support": {"x": safe, "y": sup_y, "w": cw, "h": sup_h, "size": [round(w*0.04), round(w*0.055)], "maxLines": 3, "lineHeight": 1.3, "color": "dark", "align": "center"},
        "cta": {"x": round((w-ctaW)/2), "y": cta_y, "w": ctaW, "h": cta_h, "size": [round(cta_h*0.32), round(cta_h*0.42)], "maxLines": 1, "radius": 8, "bg": "accent", "color": "dark", "align": "center", "valign": "middle", "weight": "bold"},
        "safeZone": {"top": top, "bottom": h - bottom, "note": "Meta 9:16: top 14% / bottom 35% reserved for platform UI"},
    }

count = 0
for f in glob.glob('src/templates/T*.json'):
    d = json.load(open(f))
    if '1080x1920' in d['sizes']:
        d['sizes']['1080x1920'] = safe_vertical_9x16(1080, 1920)
        count += 1
    json.dump(d, open(f, 'w'), indent=2)
print('rewrote', count, '9:16 layouts')

d = json.load(open('src/templates/T01.json'))
s = d['sizes']['1080x1920']
for role in ['logo', 'headline', 'support', 'cta']:
    e = s[role]; end = e['y'] + e['h']
    ok = 'OK' if 269 <= e['y'] and end <= 1248 else 'VIOLATION'
    print('  ' + role + ': ' + str(e['y']) + '..' + str(end) + ' ' + ok)

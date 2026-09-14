"""Client-owned YouTube connections and publishing workbench."""

def register_youtube_studio(app):
    from .app import bp, public_bp
    app.register_blueprint(bp)
    app.register_blueprint(public_bp)


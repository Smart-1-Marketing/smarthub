"""Durable-job recovery for a scheduler or an operator, without a browser tab."""
import click

from .db import db
from .models import Scene
from .routes.heygen import spokesperson_status


def install(app):
    @app.cli.command("commercial-recover-presenters")
    @click.option("--limit", default=50, type=click.IntRange(1, 500))
    def recover(limit):
        """Poll existing HeyGen jobs and retry storing completed clips. Never generates."""
        ids = []
        for scene in Scene.query.filter(Scene.asset_meta_json.isnot(None)).yield_per(100):
            meta = scene.asset_meta or {}
            job = meta.get("heygen_job") or {}
            pending = job.get("status") in ("pending", "processing", "failed") and not job.get("provider_terminal")
            if (job.get("job_id") or job.get("recovered")) and (pending or meta.get("spokesperson_mirrored") is False):
                ids.append((scene.project_id, scene.id))
                if len(ids) >= limit:
                    break
        for project_id, scene_id in ids:
            try:
                response = spokesperson_status(project_id, scene_id)
                if isinstance(response, tuple):
                    response = response[0]
                result = response.get_json()
                click.echo(f"scene {scene_id}: {result.get('status', 'unknown')}; storage pending={result.get('storage_pending', False)}")
            except Exception:
                db.session.rollback()
                click.echo(f"scene {scene_id}: recovery could not finish; retry later", err=True)

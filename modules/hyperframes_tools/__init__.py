"""Paint Animation and Vox Explainer — the two HyperFrames skills, standalone.

Both are also reachable inside the Commercial Builder: paint animation as a
scene's visual source, and a Vox explainer as its own commercial type. This
module is the other half — the quick job, with no client, no brief, no QC and
no wizard, for the times somebody wants one clip rather than a spot. Same
weight class as the Image Creator's generate panel or the Background Remover,
and the same convention: a client is optional, and picking one is what puts
the result on their record.

**One module, two tools, because they are two skins on one render service.**
Submitting, polling, storing the job and filing the finished file are
identical for both; only the parameters differ. Two directories would be two
copies of that, and the next fix to the poll would land in one of them —
which is the drift `hub/storage.py` exists to stop.

**Templates are prefixed `hf_`.** These are blueprints on the hub app, so they
share the hub's Jinja environment, and a bare `index.html` here would be
resolved against the hub's own templates first and then each blueprint's
folder in registration order. Calculators and Page Image Optimizer each
shipped a plain `index.html` and `/tools/page-images/` rendered the
calculator's — the trap `/api/integrity` now has a high-severity check for.
"""

from .app import paint_bp, vox_bp, register  # noqa: F401

__all__ = ["paint_bp", "vox_bp", "register"]

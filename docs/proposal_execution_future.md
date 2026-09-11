# Future cleanup

Once the workflow is accepted, move the runtime Sales-nav insertion into `hub/sidebar.py`, the Client 360 shortcut into its proposal-row renderer, and the scheduler bridge into a dedicated `hub.scheduler.JOBS` entry. Those are presentation/registration cleanups only; the persisted runs, tasks, approvals and adapters remain unchanged.

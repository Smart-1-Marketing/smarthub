# MVP implementation notes

Proposal Execution registers with the Hub during QA workflow registration so its SQLAlchemy models exist before the Hub's shared `create_all()` call. The route module also installs the scheduler bridge and adds the Sales navigation row and Client 360 Execute shortcut at runtime. Those integration shims can be moved into the central registries after the MVP is proven without changing the execution engine or database model.

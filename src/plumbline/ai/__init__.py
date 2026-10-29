"""Model-backed features with typed outputs, validation, and recorded runs.

Every feature here has an offline path that needs no model, and a model path that is measured on a
held-out eval. Model calls are recorded (prompt hash -> output) so a run made with a key can be
replayed exactly by the tests and the public app, which never hold a key.
"""

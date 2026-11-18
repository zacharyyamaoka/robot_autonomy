"""The many ways an intervention is seen. Each adapter is a function returning plain records; `ingest` writes them.

- `labelled.read_labelled`: any labelled log of asks (a chat labeller's output, a hook's ledger, an operator's log)
- `robot_activity.read_blocks` / `read_faults`: the Robot Activity Agent's frozen files (never imports robot_activity)
- `diagnostics.read_diagnostics`: robot_msgs DiagnosticRecord streams (the only module that imports robot_msgs)
- `field_service.read_tickets`: repair / service tickets from a deployed site

Adapters never write. Nothing here is imported eagerly, so `import robot_autonomy.sources` never pulls robot_msgs.
"""

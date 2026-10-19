"""Tier 2 — Executive Playbook engine.

Curated questions (semantic/executive_playbook.yaml) are answered at each month-end by deterministic,
reconciling analyses over governed SQL templates; results are saved as versioned briefing snapshots that a
reviewer approves before executives see them. Depends on bi_service; never the reverse. No account ids leave Athena.
"""

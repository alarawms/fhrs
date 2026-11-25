"""Harvest pipeline: store payload → append event → normalize → dedup → merge
(PROMPT.md N1–N4). The pipeline never erases; conflicts resolve by source recency
with an audit row.
"""

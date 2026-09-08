"""Compact read-only viewer for text log files."""

from __future__ import annotations

import html

import streamlit as st


def render_log_viewer(text: str, *, height: int = 300) -> None:
    """Render log text in a resizable, scrollable, read-only viewport."""
    safe_text = html.escape(text or "(empty file)")
    height_px = max(120, int(height))
    st.markdown(
        f"""
<pre class="molscout-log-viewer" style="height: {height_px}px;" aria-label="Log output">{safe_text}</pre>
<style>
pre.molscout-log-viewer {{
  width: 100%;
  min-height: 120px;
  max-height: 80vh;
  resize: vertical;
  overflow: auto;
  box-sizing: border-box;
  padding: 0.65rem 0.75rem;
  border: 1px solid rgba(128, 128, 128, 0.35);
  border-radius: 0.5rem;
  background: transparent;
  color: inherit;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 0.85rem;
  line-height: 1.35;
  white-space: pre; /* textareaのwrap="off"に相当 */
  margin: 0;
}}
pre.molscout-log-viewer:focus {{
  outline: 1px solid rgba(128, 128, 128, 0.45);
  outline-offset: 0;
}}
</style>
""",
        unsafe_allow_html=True,
    )

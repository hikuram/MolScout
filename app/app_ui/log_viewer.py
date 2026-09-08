"""Compact read-only viewer for text log files."""

from __future__ import annotations

import html

import streamlit as st


def render_log_viewer(text: str, key: str, *, height: int = 300) -> None:
    """Render log text in a resizable, scrollable, read-only viewport."""
    
    st.text_area(
        "Log content", 
        value=text or "(empty file)", 
        height=int(height), 
        disabled=True, 
        label_visibility="collapsed", 
        key=key
    )
    
    st.html(f"""
    <style>
    .st-key-{key} textarea:disabled {{
        cursor: text !important;
        -webkit-text-fill-color: var(--st-text-color) !important;
        color: var(--st-text-color) !important;
    }}
    .st-key-{key} div[data-baseweb="textarea"] {{
        background-color: transparent !important;
        opacity: 1 !important;
        border-color: var(--st-border-color) !important;
    }}
    </style>
    """)

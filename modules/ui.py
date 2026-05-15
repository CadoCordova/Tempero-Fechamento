import streamlit as st

PRIMARY_COLOR = "#F06BAA"
BACKGROUND_SOFT = "#FDF2F7"
TEXT_DARK = "#333333"


def inject_css():
    st.markdown(
        f"""
        <style>
        .block-container {{
            max-width: 1200px;
            padding-top: 3.5rem;
            padding-bottom: 2.5rem;
        }}
        body {{
            background-color: {BACKGROUND_SOFT};
        }}
        .tempero-title {{
            font-size: 1.8rem;
            font-weight: 800;
            color: {PRIMARY_COLOR};
            margin-bottom: 0.3rem;
            text-align: center;
        }}
        .tempero-subtitle {{
            font-size: 0.95rem;
            color: #666666;
            margin-bottom: 1.2rem;
            text-align: center;
        }}
        .tempero-card {{
            background-color: #FFFFFF;
            padding: 1.1rem 1.3rem;
            border-radius: 0.8rem;
            box-shadow: 0 2px 6px rgba(0,0,0,0.05);
            margin-bottom: 0.8rem;
        }}
        .tempero-metric-card {{
            background: linear-gradient(135deg, {PRIMARY_COLOR}, #e04592);
            color: white !important;
            padding: 0.9rem 1.1rem;
            border-radius: 0.8rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.18);
        }}
        .tempero-metric-label {{
            font-size: 0.85rem;
            opacity: 0.9;
        }}
        .tempero-metric-value {{
            font-size: 1.4rem;
            font-weight: 700;
        }}
        .tempero-section-title {{
            font-weight: 700;
            color: {TEXT_DARK};
            margin-bottom: 0.4rem;
        }}
        .tempero-section-sub {{
            font-size: 0.85rem;
            color: #777777;
            margin-bottom: 0.6rem;
        }}
        .stTabs [role="tab"] {{
            padding: 0.6rem 1rem;
            border-radius: 999px;
            color: #555 !important;
        }}
        .stTabs [role="tab"][aria-selected="true"] {{
            background-color: {PRIMARY_COLOR}20 !important;
            color: {PRIMARY_COLOR} !important;
            border-bottom-color: transparent !important;
        }}
        .tempero-alerta {{
            background-color: #FFF3CD;
            border-left: 4px solid #FFC107;
            padding: 0.8rem;
            margin: 0.5rem 0;
            border-radius: 0.4rem;
            font-size: 0.9rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def metric_card_html(label: str, value: str) -> str:
    return f"""
    <div class="tempero-metric-card">
      <div class="tempero-metric-label">{label}</div>
      <div class="tempero-metric-value">{value}</div>
    </div>
    """

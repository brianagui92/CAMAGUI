import streamlit as st
import pandas as pd
import psycopg2
import os
import numpy as np
from dotenv import load_dotenv
import plotly.express as px

try:
    from scipy.optimize import curve_fit
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# 1. Page Configuration
st.set_page_config(page_title="CAMAGUI Dashboard", page_icon="🦐", layout="wide")

# Load environment variables
load_dotenv()

# 2. Database Connection Wrapper
def get_connection():
    if 'db_conn' not in st.session_state or st.session_state.db_conn.closed != 0:
        st.session_state.db_conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    else:
        try:
            with st.session_state.db_conn.cursor() as cur:
                cur.execute("SELECT 1")
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            st.session_state.db_conn = psycopg2.connect(os.getenv("DATABASE_URL"))
            
    return st.session_state.db_conn

# 3. Data Fetching
@st.cache_data(ttl=600) 
def load_bio_data():
    conn = get_connection()
    query = "SELECT * FROM vw_cycle_performance_bio;"
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        return pd.read_sql(query, conn)

@st.cache_data(ttl=3600)
def load_products():
    conn = get_connection()
    query = "SELECT product_id, name FROM products WHERE category = 'feed';"
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        return pd.read_sql(query, conn)

@st.cache_data(ttl=60)
def load_active_cycles():
    conn = get_connection()
    query = """
        SELECT DISTINCT ON (p.pond_name) 
            p.pond_name, 
            gc.cycle_code, 
            gc.cycle_id 
        FROM growout_cycles gc
        JOIN ponds p ON gc.pond_id = p.pond_id
        ORDER BY p.pond_name, gc.stocking_date DESC;
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        return pd.read_sql(query, conn)

@st.cache_data(ttl=600)
def load_growth_curve_data():
    conn = get_connection()
    query = """
        WITH cycle_base AS (
            SELECT gc.cycle_id, gc.cycle_code, gc.stocking_date, p.pond_name
            FROM growout_cycles gc
            JOIN ponds p ON gc.pond_id = p.pond_id
            WHERE gc.stocking_date >= '2025-09-01'
        ),
        logs AS (
            SELECT cb.cycle_code, cb.pond_name, wpl.log_date, 
                   (wpl.log_date - cb.stocking_date) AS doc, wpl.actual_weight_g,
                   'Weekly Log' as point_type
            FROM weekly_pond_logs wpl
            JOIN cycle_base cb ON wpl.cycle_id = cb.cycle_id
            WHERE wpl.actual_weight_g IS NOT NULL AND wpl.actual_weight_g > 0
        ),
        harvest_pts AS (
            SELECT cb.cycle_code, cb.pond_name, h.harvest_date AS log_date,
                   (h.harvest_date - cb.stocking_date) AS doc, h.average_weight_g AS actual_weight_g,
                   'Final Harvest' as point_type
            FROM harvests h
            JOIN cycle_base cb ON h.cycle_id = cb.cycle_id
            WHERE h.average_weight_g IS NOT NULL AND h.average_weight_g > 0
        ),
        transfer_pts AS (
            SELECT cb.cycle_code, cb.pond_name, pt.transfer_date AS log_date,
                   (pt.transfer_date - cb.stocking_date) AS doc, 
                   pt.transfer_weight_g AS actual_weight_g,
                   'Transfer (Day 0)' as point_type
            FROM precria_transfers pt
            JOIN cycle_base cb ON pt.cycle_id = cb.cycle_id
            WHERE pt.transfer_weight_g IS NOT NULL AND pt.transfer_weight_g > 0
        )
        SELECT * FROM logs
        UNION ALL
        SELECT * FROM harvest_pts
        UNION ALL
        SELECT * FROM transfer_pts
        ORDER BY doc ASC;
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        return pd.read_sql(query, conn)

# --- START OF UI ---
st.title("🦐 CAMAGUI Master Dashboard")

tab1, tab2 = st.tabs(["📊 Performance & Operations", "🔬 Advanced Analytics"])

with tab1:
    st.markdown("Welcome to the unified analytics and data entry platform.")

    try:
        df = load_bio_data()
        
        if 'total_lbs_remitidas' in df.columns:
            df['total_lbs_remitidas'] = df['total_lbs_remitidas'].fillna(0)
        
        MAX_REALISTIC_DENSITY = 500000 
        if 'stocking_density_ha' in df.columns:
            df = df[(df['stocking_density_ha'] <= MAX_REALISTIC_DENSITY) | (df['stocking_density_ha'].isna())]
        
        st.header("Cycle Performance Snapshot")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Cycles Tracked", len(df))
        
        avg_fcr = df['fcr'].mean()
        col2.metric("Average FCR", f"{avg_fcr:.2f}" if pd.notna(avg_fcr) else "N/A")
        
        avg_survival = df['survival_rate_pct'].mean()
        col3.metric("Avg Survival Rate", f"{avg_survival:.1f}%" if pd.notna(avg_survival) else "N/A")
        
        total_lbs = df['total_lbs_remitidas'].sum()
        col4.metric("Total Lbs Harvested", f"{total_lbs:,.0f}" if pd.notna(total_lbs) else "0")

        st.divider()
        col_chart, col_filter = st.columns([3, 1]) 
        
        with col_filter:
            st.subheader("Filters")
            pond_list_dash = ["All Ponds"] + sorted(df['pond_name'].dropna().unique().tolist())
            selected_pond_dash = st.selectbox("Select Pond Filter", pond_list_dash)

        with col_chart:
            st.subheader("📊 Growth vs Stocking Density")
            
            if selected_pond_dash == "All Ponds":
                filtered_df = df
            else:
                filtered_df = df[df['pond_name'] == selected_pond_dash]

            if not filtered_df.empty:
                fig = px.scatter(
                    filtered_df, 
                    x="stocking_density_ha", 
                    y="avg_weekly_growth_g", 
                    color="pond_name",
                    size="total_lbs_remitidas",
                    hover_data=["cycle_code", "fcr", "survival_rate_pct"],
                    title=f"Growth vs Density ({selected_pond_dash})",
                    labels={
                        "stocking_density_ha": "Stocking Density (Animals / Ha)",
                        "avg_weekly_growth_g": "Avg Weekly Growth (g / week)",
                        "pond_name": "Pond"
                    }
                )
                fig.update_layout(xaxis=dict(rangemode="tozero"), yaxis=dict(rangemode="tozero"))
                if len(filtered_df) == 1:
                    fig.update_traces(marker=dict(size=20))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info(f"No data available for {selected_pond_dash}")
            
    except Exception as e:
        st.error(f"Could not load data. Error: {e}")

    st.divider()
    st.header("📝 Log Weekly Pond Data")

    active_cycles_df = load_active_cycles()
    pond_options = sorted(active_cycles_df['pond_name'].tolist()) if not active_cycles_df.empty else []

    product_df = load_products()
    product_mapping = dict(zip(product_df['name'], product_df['product_id'])) if not product_df.empty else {}
    product_options = ["(Select Feed)"] + list(product_mapping.keys())

    with st.form("weekly_log_form"):
        st.write("Submit the latest realities from the farm.")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            selected_pond_form = st.selectbox("Select Pond*", pond_options)
            log_date = st.date_input("Log Date*")
        with col2:
            ultimo_tope_kg = st.number_input("Último Tope (kg)", min_value=0.0, step=10.0)
            feed_consumed_kg = st.number_input("Feed Consumed / Week (kg)*", min_value=0.0, step=25.0)
        with col3:
            selected_product = st.selectbox("Feed Product Type*", product_options)
            actual_weight_g = st.number_input("Current Weight (g)*", min_value=0.0, step=0.1)
        with col4:
            st.text_area("Comments", height=120)
            submitted = st.form_submit_button("Submit Log Update", type="primary")
        
        if submitted:
            if selected_product == "(Select Feed)":
                st.error("Please select a valid Feed Product Type!")
            else:
                pond_cycle_row = active_cycles_df[active_cycles_df['pond_name'] == selected_pond_form].iloc[0]
                cycle_id = int(pond_cycle_row['cycle_id'])
                cycle_code = pond_cycle_row['cycle_code']
                
                product_id = product_mapping[selected_product]
                
                try:
                    active_conn = get_connection()
                    with active_conn.cursor() as cur:
                        query = """
                            INSERT INTO weekly_pond_logs (cycle_id, log_date, ultimo_tope_kg, feed_consumed_kg, product_id, actual_weight_g)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (cycle_id, log_date) DO UPDATE SET
                                ultimo_tope_kg = EXCLUDED.ultimo_tope_kg,
                                feed_consumed_kg = EXCLUDED.feed_consumed_kg,
                                product_id = EXCLUDED.product_id,
                                actual_weight_g = EXCLUDED.actual_weight_g;
                        """
                        cur.execute(query, (cycle_id, log_date, ultimo_tope_kg, feed_consumed_kg, product_id, actual_weight_g))
                    
                    active_conn.commit()
                    st.success(f"✅ Successfully logged data! Recorded under active cycle **{cycle_code}** for **{selected_pond_form}** on {log_date}.")
                    st.cache_data.clear()
                    
                except Exception as e:
                    if 'active_conn' in locals() and not active_conn.closed:
                        active_conn.rollback()
                    st.error(f"❌ Failed to submit log. Error: {e}")

with tab2:
    st.header("Empirical Growth Modeling: Pond Variance Analytics")
    st.write("Generates individual biological curves for each pond to visually compare high-performance outliers against the Central Farm Masterline.")
    
    growth_df = load_growth_curve_data()
    
    if not HAS_SCIPY:
        st.error("⚠️ **Missing Math Library!** Please run `pip install scipy` in your terminal to enable Advanced VBGF logic.")
    
    if not growth_df.empty and HAS_SCIPY:
        growth_df = growth_df[(growth_df['doc'] >= 0) & (growth_df['doc'] < 200)]
        growth_df = growth_df[growth_df['actual_weight_g'] < 70] 
        
        # Color mapping to ensure line colors perfectly match the plotting scatter dots
        unique_ponds = sorted(growth_df['pond_name'].dropna().unique())
        theme_colors = px.colors.qualitative.Plotly + px.colors.qualitative.G10 + px.colors.qualitative.D3
        color_map = {pond: theme_colors[i % len(theme_colors)] for i, pond in enumerate(unique_ponds)}
        
        fig2 = px.scatter(
            growth_df, x="doc", y="actual_weight_g", color="pond_name", 
            color_discrete_map=color_map, opacity=0.3, # Darken dots slightly to make lines pop
            symbol="point_type",
            hover_data=["cycle_code", "point_type"],
            labels={"doc": "Pond DOC (Days from Transfer)", "actual_weight_g": "Actual Shrimp Weight (g)", "pond_name": "Pond"}
        )
        
        if len(growth_df) > 5:
            def vbgf_gen(t, Winf, k, t0, b):
                bracket = np.maximum(0.0, 1.0 - np.exp(-k * (t - t0)))
                return Winf * (bracket ** b)
            
            x_trend = np.linspace(-25, 150, 100)
            
            # --- 1. GLOBAL MASTERLINE FIT ---
            try:
                popt_global, _ = curve_fit(
                    vbgf_gen, growth_df['doc'], growth_df['actual_weight_g'], 
                    p0=[65.0, 0.02, -21.0, 2.0], 
                    bounds=([55.0, 0.001, -35.0, 1.0], [90.0, 0.05, -10.0, 3.5]),
                    maxfev=15000
                )
                
                def p_global(x): return vbgf_gen(x, *popt_global)
                y_trend_global = p_global(x_trend)
                
                # Thick White Dashed Line for the Farm Average
                fig2.add_scatter(x=x_trend, y=y_trend_global, mode='lines', name='GLOBAL MASTERLINE', line=dict(color='white', width=5, dash='dash'))
                max_capacity_g = popt_global[0]
                formula_str = f"W_g = {popt_global[0]:.1f} \\cdot \\left[1 - e^{{-{popt_global[1]:.4f} \\cdot (DOC - ({popt_global[2]:.1f}))}}\\right]^{{{popt_global[3]:.2f}}}"
            except Exception:
                max_capacity_g = 0
                formula_str = "Error"
                popt_global = None

            # --- 2. INDIVIDUAL POND FITS ---
            pond_metrics = []
            for pond in unique_ponds:
                p_df = growth_df[growth_df['pond_name'] == pond]
                # Only fit ponds that have enough data points to legally form an S-Curve
                if len(p_df) >= 4:
                    try:
                        p_popt, _ = curve_fit(
                            vbgf_gen, p_df['doc'], p_df['actual_weight_g'], 
                            p0=[65.0, 0.02, -21.0, 2.0], 
                            bounds=([55.0, 0.001, -35.0, 1.0], [90.0, 0.05, -10.0, 3.5]),
                            maxfev=10000
                        )
                        # Plot thin line matching the exact pond's color
                        y_p = vbgf_gen(x_trend, *p_popt)
                        fig2.add_scatter(x=x_trend, y=y_p, mode='lines', name=f'{pond} Curve', line=dict(color=color_map[pond], width=2))
                        
                        # Add to the mathematical comparison tracking matrix
                        pond_metrics.append({
                            "Pond": pond,
                            "Est. Max Ceiling (g)": f"{p_popt[0]:.1f}",
                            "Growth Coefficient (k)": f"{p_popt[1]:.4f}",
                            "Shape Phase (b)": f"{p_popt[3]:.2f}",
                            "Theo. Precría Day (t0)": f"{p_popt[2]:.1f}"
                        })
                    except Exception:
                        pass # Ignore ponds that mathematically fail to fit the boundary constraints
            
            # --- VISUAL ENHANCEMENTS ---
            if popt_global is not None:
                fig2.add_vrect(
                    x0=popt_global[2], x1=0, 
                    fillcolor="rgba(0, 212, 255, 0.08)", line_width=1, line_dash="dash",
                    annotation_text="Average Biological Nursery (Precría)", annotation_position="top left"
                )
        
        fig2.update_layout(xaxis=dict(range=[-25, 150]), yaxis=dict(rangemode="tozero"))
        st.plotly_chart(fig2, use_container_width=True)
        
        if len(growth_df) > 5 and max_capacity_g > 0:
            # Layout the Page into two distinct physical sections below the graph
            st.divider()
            col_matrix, col_table = st.columns([1, 1.5])
            
            with col_matrix:
                st.subheader("🔬 Pond Variance Matrix")
                st.write("Compare the exact parameters the physics engine calculated for each specific pond environment.")
                if pond_metrics:
                    pm_df = pd.DataFrame(pond_metrics)
                    st.dataframe(pm_df, use_container_width=True, hide_index=True)
                else:
                    st.info("Not enough data to calculate distinct individual curves yet.")
                
                st.info(f"**Farm Global Masterline:** \n\n $$ {formula_str} $$")
            
            with col_table:
                st.subheader("🎯 Global Baseline Projections")
                st.write("Using the Farm Masterline (White Dashed Line) to map empirical weekly target minimums.")
                
                def exact_doc_from_weight_gen(w, Winf, k, t0, b):
                    ratio = (w / Winf) ** (1.0 / b)
                    return t0 - (1.0 / k) * np.log(1.0 - ratio)

                projection_data = []
                for target_weight in np.arange(0.5, 40.5, 0.5):
                    if target_weight >= max_capacity_g - 0.2:
                        continue
                        
                    estimated_pond_doc = exact_doc_from_weight_gen(target_weight, *popt_global)
                    biological_age = estimated_pond_doc - popt_global[2] 
                    
                    projected_day = estimated_pond_doc + 7
                    weight_next_week = p_global(projected_day)
                    
                    weekly_gain = weight_next_week - target_weight
                    daily_gain = weekly_gain / 7.0
                    
                    projection_data.append({
                        "Current Avg Weight (g)": f"{target_weight:.1f}",
                        "Est. Pond DOC": f"{estimated_pond_doc:.0f}",
                        "True Biol. Age (Days)": f"{biological_age:.0f}",
                        "Expected Weekly Gain (g)": f"{weekly_gain:.2f}",
                        "Target Next Week (g)": f"{weight_next_week:.2f}"
                    })

                proj_df = pd.DataFrame(projection_data)
                st.dataframe(proj_df, use_container_width=True, height=500)
    elif not HAS_SCIPY:
        pass
    else:
        st.warning("No valid growth data found for cycles starting after Sept 2025.")
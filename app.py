import streamlit as st
import pandas as pd
import psycopg2
import os
import numpy as np
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import plotly.express as px
import plotly.graph_objects as go

try:
    from scipy.optimize import curve_fit
    from scipy.interpolate import interp1d
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    from supabase import create_client, Client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False

# 1. Page Configuration
st.set_page_config(page_title="CAMAGUI Dashboard", page_icon="🦐", layout="wide")

# Load environment variables
load_dotenv()

# --- AUTHENTICATION LAYER ---
if HAS_SUPABASE:
    SB_URL = os.getenv("SUPABASE_URL")
    SB_KEY = os.getenv("SUPABASE_KEY")
    
    if SB_URL and SB_KEY:
        supabase_client: Client = create_client(SB_URL, SB_KEY)
        
        if 'auth_user' not in st.session_state:
            st.session_state.auth_user = None
            
        if not st.session_state.auth_user:
            st.title("🔒 CAMAGUI Security System")
            st.write("Please authenticate to access the master dashboard.")
            
            with st.form("login_form"):
                email = st.text_input("Email Address")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Secure Login")
                
                if submitted:
                    try:
                        res = supabase_client.auth.sign_in_with_password({"email": email, "password": password})
                        if res.user:
                            st.session_state.auth_user = res.user
                            st.rerun()
                    except Exception as e:
                        st.error("❌ Authentication Failed: Invalid email or password.")
            
            st.stop() # Halts the rendering of the dashboard if not logged in
        else:
            with st.sidebar:
                st.write(f"👤 Logged in as: **{st.session_state.auth_user.email}**")
                if st.button("Logout"):
                    supabase_client.auth.sign_out()
                    st.session_state.auth_user = None
                    st.rerun()
    else:
        st.sidebar.warning("⚠️ Supabase API Keys missing. Running in open developer mode.")
else:
    st.sidebar.warning("⚠️ Supabase library not found. Running in open developer mode.")


# 2. Database Connection Wrapper (HARDENED FOR FAILED TRANSACTIONS)
def get_connection():
    if 'db_conn' not in st.session_state or st.session_state.db_conn.closed != 0:
        st.session_state.db_conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    else:
        try:
            with st.session_state.db_conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            try:
                st.session_state.db_conn.rollback()
            except Exception:
                pass
            st.session_state.db_conn = psycopg2.connect(os.getenv("DATABASE_URL"))
            
    return st.session_state.db_conn


def sync_model_to_sql(model_name, popt):
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO growth_models (model_name, w_inf, k, t0, b)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (model_name) DO UPDATE SET
                    w_inf = EXCLUDED.w_inf, k = EXCLUDED.k, t0 = EXCLUDED.t0, 
                    b = EXCLUDED.b, updated_at = CURRENT_TIMESTAMP;
            """, (model_name, popt[0], popt[1], popt[2], popt[3]))
        conn.commit()
    except Exception:
        if 'conn' in locals() and not conn.closed:
            try:
                conn.rollback()
            except Exception:
                pass

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
    return pd.read_sql("SELECT product_id, name FROM products WHERE category = 'feed';", conn)

@st.cache_data(ttl=60)
def load_active_cycles():
    conn = get_connection()
    query = """
        SELECT DISTINCT ON (p.pond_name) p.pond_name, gc.cycle_code, gc.cycle_id 
        FROM growout_cycles gc JOIN ponds p ON gc.pond_id = p.pond_id
        ORDER BY p.pond_name, gc.stocking_date DESC;
    """
    return pd.read_sql(query, conn)

@st.cache_data(ttl=60)
def load_camagui_ponds():
    conn = get_connection()
    query = """
        SELECT p.pond_id, p.pond_name 
        FROM ponds p JOIN farms f ON p.farm_id = f.farm_id 
        WHERE f.name ILIKE '%camagui%'
        ORDER BY p.pond_name;
    """
    return pd.read_sql(query, conn)

@st.cache_data(ttl=60)
def load_daily_feed_history():
    try:
        conn = get_connection()
        query = """
            SELECT dfl.log_id, dfl.feed_date as "Date", p.pond_name as "Pond", 
            dfl.quantity_kg as "Kilos", dfl.do_am as "DO AM", dfl.do_sat_am as "Sat AM %", 
            dfl.temp_am as "Temp AM", dfl.do_pm as "DO PM", dfl.do_sat_pm as "Sat PM %", dfl.temp_pm as "Temp PM"
            FROM daily_feed_logs dfl 
            JOIN ponds p ON dfl.pond_id = p.pond_id
            ORDER BY dfl.feed_date DESC, p.pond_name;
        """
        return pd.read_sql(query, conn)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=600)
def load_growth_curve_data():
    conn = get_connection()
    query = """
        WITH cycle_base AS (
            SELECT gc.cycle_id, gc.cycle_code, gc.stocking_date, p.pond_name
            FROM growout_cycles gc JOIN ponds p ON gc.pond_id = p.pond_id
            WHERE gc.stocking_date >= '2025-09-01'
        ),
        logs AS (
            SELECT cb.cycle_code, cb.pond_name, wpl.log_date, (wpl.log_date - cb.stocking_date) AS doc, wpl.actual_weight_g, 'Weekly Log' as point_type
            FROM weekly_pond_logs wpl JOIN cycle_base cb ON wpl.cycle_id = cb.cycle_id
            WHERE wpl.actual_weight_g IS NOT NULL AND wpl.actual_weight_g > 0
        ),
        harvest_pts AS (
            SELECT cb.cycle_code, cb.pond_name, h.harvest_date AS log_date, (h.harvest_date - cb.stocking_date) AS doc, h.average_weight_g AS actual_weight_g, 'Final Harvest' as point_type
            FROM harvests h JOIN cycle_base cb ON h.cycle_id = cb.cycle_id
            WHERE h.average_weight_g IS NOT NULL AND h.average_weight_g > 0
        ),
        transfer_pts AS (
            SELECT cb.cycle_code, cb.pond_name, pt.transfer_date AS log_date, (pt.transfer_date - cb.stocking_date) AS doc, pt.transfer_weight_g AS actual_weight_g, 'Transfer (Day 0)' as point_type
            FROM precria_transfers pt JOIN cycle_base cb ON pt.cycle_id = cb.cycle_id
            WHERE pt.transfer_weight_g IS NOT NULL AND pt.transfer_weight_g > 0
        )
        SELECT * FROM logs UNION ALL SELECT * FROM harvest_pts UNION ALL SELECT * FROM transfer_pts ORDER BY doc ASC;
    """
    return pd.read_sql(query, conn)

# NEW DIAGNOSTIC FUNCTIONS
@st.cache_data(ttl=600)
def load_cycle_dropdown():
    conn = get_connection()
    return pd.read_sql("SELECT gc.cycle_id, p.pond_name || ' - ' || gc.cycle_code as display_name FROM growout_cycles gc JOIN ponds p ON gc.pond_id=p.pond_id ORDER BY gc.stocking_date DESC;", conn)

@st.cache_data(ttl=60)
def get_cycle_diagnostic_data(cycle_id):
    conn = get_connection()
    transfer = pd.read_sql("SELECT transfer_date, animals_transferred, transfer_weight_g FROM precria_transfers WHERE cycle_id = %s", conn, params=(cycle_id,))
    harvests = pd.read_sql("SELECT harvest_date, harvest_type, lbs_remitidas AS total_lbs, average_weight_g FROM harvests WHERE cycle_id = %s ORDER BY harvest_date", conn, params=(cycle_id,))
    logs = pd.read_sql("SELECT log_date, actual_weight_g FROM weekly_pond_logs WHERE cycle_id = %s AND actual_weight_g IS NOT NULL ORDER BY log_date", conn, params=(cycle_id,))
    return transfer, harvests, logs

# --- START OF UI ---
st.title("🦐 CAMAGUI Master Dashboard")

tab_daily, tab1, tab2, tab3 = st.tabs(["📝 Ingreso de Alimento Diario", "📊 Performance & Operations", "🔬 Advanced Analytics", "🧪 Survival Diagnostics"])

with tab_daily:
    st.header("📝 Ingreso Rápido Diario (Alimento y Parámetros)")
    st.write("Ingrese los kilos de alimento y las lecturas de calidad de agua (AM y PM) para cada piscina.")
    
    col_date, _ = st.columns([1, 3])
    with col_date:
        selected_date = st.date_input("Fecha de Registro", max_value=date.today())
        
    camagui_ponds = load_camagui_ponds()
    if not camagui_ponds.empty:
        input_df = pd.DataFrame({
            "Piscina": camagui_ponds['pond_name'].tolist(),
            "Kilos": [0.0] * len(camagui_ponds),
            "DO AM": [None] * len(camagui_ponds),
            "Sat AM %": [None] * len(camagui_ponds),
            "Temp AM": [None] * len(camagui_ponds),
            "DO PM": [None] * len(camagui_ponds),
            "Sat PM %": [None] * len(camagui_ponds),
            "Temp PM": [None] * len(camagui_ponds),
            "pond_id": camagui_ponds['pond_id'].tolist()
        })
        
        st.write(f"**Ingrese datos para: {selected_date.strftime('%d/%m/%Y')}**")
        edited_df = st.data_editor(
            input_df[["Piscina", "Kilos", "DO AM", "Sat AM %", "Temp AM", "DO PM", "Sat PM %", "Temp PM"]],
            column_config={
                "Piscina": st.column_config.TextColumn("Piscina", disabled=True),
                "Kilos": st.column_config.NumberColumn("Kilos Alimento", min_value=0.0, format="%.1f"),
                "DO AM": st.column_config.NumberColumn("Oxígeno AM", min_value=0.0, format="%.2f"),
                "Sat AM %": st.column_config.NumberColumn("Sat AM %", min_value=0.0, format="%.1f"),
                "Temp AM": st.column_config.NumberColumn("Temp AM", min_value=0.0, format="%.1f"),
                "DO PM": st.column_config.NumberColumn("Oxígeno PM", min_value=0.0, format="%.2f"),
                "Sat PM %": st.column_config.NumberColumn("Sat PM %", min_value=0.0, format="%.1f"),
                "Temp PM": st.column_config.NumberColumn("Temp PM", min_value=0.0, format="%.1f")
            },
            hide_index=True,
            num_rows="fixed",
            key="feed_entry_grid",
            use_container_width=True
        )
        
        if st.button("Guardar Datos del Día", type="primary"):
            if 'auth_user' in st.session_state and st.session_state.auth_user or (not HAS_SUPABASE or not (SB_URL and SB_KEY)):
                try:
                    conn = get_connection()
                    with conn.cursor() as cur:
                        for i, row in edited_df.iterrows():
                            kilos = row["Kilos"] if pd.notnull(row["Kilos"]) else 0.0
                            if kilos > 0 or pd.notnull(row["DO AM"]) or pd.notnull(row["DO PM"]):
                                pond_id = input_df.iloc[i]["pond_id"]
                                cur.execute('''
                                    INSERT INTO daily_feed_logs 
                                    (pond_id, feed_date, quantity_kg, do_am, do_sat_am, temp_am, do_pm, do_sat_pm, temp_pm) 
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) 
                                    ON CONFLICT (pond_id, feed_date) 
                                    DO UPDATE SET 
                                        quantity_kg = EXCLUDED.quantity_kg,
                                        do_am = EXCLUDED.do_am, do_sat_am = EXCLUDED.do_sat_am, temp_am = EXCLUDED.temp_am,
                                        do_pm = EXCLUDED.do_pm, do_sat_pm = EXCLUDED.do_sat_pm, temp_pm = EXCLUDED.temp_pm;
                                ''', (
                                    int(pond_id), selected_date, kilos,
                                    row["DO AM"] if pd.notnull(row["DO AM"]) else None,
                                    row["Sat AM %"] if pd.notnull(row["Sat AM %"]) else None,
                                    row["Temp AM"] if pd.notnull(row["Temp AM"]) else None,
                                    row["DO PM"] if pd.notnull(row["DO PM"]) else None,
                                    row["Sat PM %"] if pd.notnull(row["Sat PM %"]) else None,
                                    row["Temp PM"] if pd.notnull(row["Temp PM"]) else None
                                ))
                    conn.commit()
                    st.success("✅ ¡Datos guardados exitosamente!")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error al guardar: {e}")
            else:
                st.error("Authentication required to submit data.")
    else:
        st.warning("No se encontraron piscinas configuradas para CAMAGUI.")
        
    st.divider()
    st.subheader("📚 Historial Diario")
    history_df = load_daily_feed_history()
    if not history_df.empty:
        st.write("Puedes editar cualquier valor directamente en la tabla o seleccionar la fila a la izquierda y presionar **Suprimir/Borrar** para eliminarla.")
        with st.form("history_form"):
            edited_history = st.data_editor(
                history_df,
                column_config={
                    "log_id": None, 
                    "Date": st.column_config.DateColumn("Fecha", disabled=True),
                    "Pond": st.column_config.TextColumn("Piscina", disabled=True),
                    "Kilos": st.column_config.NumberColumn("Kilos", min_value=0.0, format="%.1f"),
                    "DO AM": st.column_config.NumberColumn("Oxígeno AM", min_value=0.0, format="%.2f"),
                    "Sat AM %": st.column_config.NumberColumn("Sat AM %", min_value=0.0, format="%.1f"),
                    "Temp AM": st.column_config.NumberColumn("Temp AM", min_value=0.0, format="%.1f"),
                    "DO PM": st.column_config.NumberColumn("Oxígeno PM", min_value=0.0, format="%.2f"),
                    "Sat PM %": st.column_config.NumberColumn("Sat PM %", min_value=0.0, format="%.1f"),
                    "Temp PM": st.column_config.NumberColumn("Temp PM", min_value=0.0, format="%.1f")
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="history_editor"
            )
            submit_edits = st.form_submit_button("Guardar Cambios del Historial")
            
            if submit_edits:
                changes = st.session_state["history_editor"]
                if changes.get("edited_rows") or changes.get("deleted_rows"):
                    try:
                        conn = get_connection()
                        with conn.cursor() as cur:
                            for row_idx_str, cols in changes.get("edited_rows", {}).items():
                                row_idx = int(row_idx_str)
                                log_id = int(history_df.iloc[row_idx]["log_id"])
                                
                                # Construct dynamic update
                                set_clauses = []
                                params = []
                                
                                field_map = {
                                    "Kilos": "quantity_kg", "DO AM": "do_am", "Sat AM %": "do_sat_am", 
                                    "Temp AM": "temp_am", "DO PM": "do_pm", "Sat PM %": "do_sat_pm", "Temp PM": "temp_pm"
                                }
                                
                                for col_name, db_col in field_map.items():
                                    if col_name in cols:
                                        set_clauses.append(f"{db_col} = %s")
                                        val = cols[col_name]
                                        params.append(float(val) if val is not None else None)
                                
                                if set_clauses:
                                    params.append(log_id)
                                    query = f"UPDATE daily_feed_logs SET {', '.join(set_clauses)} WHERE log_id = %s"
                                    cur.execute(query, params)
                            
                            for row_idx in changes.get("deleted_rows", []):
                                log_id = int(history_df.iloc[row_idx]["log_id"])
                                cur.execute("DELETE FROM daily_feed_logs WHERE log_id = %s", (log_id,))
                                
                        conn.commit()
                        st.success("✅ ¡Historial actualizado!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error al actualizar: {e}")
    else:
        st.info("No hay registros previos en la base de datos.")

with tab1:
    st.markdown("Welcome to the unified analytics and data entry platform.")
    try:
        df = load_bio_data()
        if 'total_lbs_remitidas' in df.columns: df['total_lbs_remitidas'] = df['total_lbs_remitidas'].fillna(0)
        
        st.header("Cycle Performance Snapshot")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Cycles Tracked", len(df))
        avg_fcr = df['fcr'].mean()
        col2.metric("Average FCR", f"{avg_fcr:.2f}" if pd.notna(avg_fcr) else "N/A")
        avg_surv = df['survival_rate_pct'].mean()
        col3.metric("Avg Survival Rate", f"{avg_surv:.1f}%" if pd.notna(avg_surv) else "N/A")
        col4.metric("Total Lbs Harvested", f"{df['total_lbs_remitidas'].sum():,.0f}")
    except Exception as e:
        st.error(f"Could not load data. Error: {e}")

    st.divider()
    st.header("📝 Log Weekly Pond Data")
    active_cycles_df = load_active_cycles()
    pond_options = sorted(active_cycles_df['pond_name'].tolist()) if not active_cycles_df.empty else []
    product_df = load_products()
    product_mapping = dict(zip(product_df['name'], product_df['product_id'])) if not product_df.empty else {}
    
    with st.form("weekly_log_form"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            selected_pond = st.selectbox("Select Pond*", pond_options)
            log_dt = st.date_input("Log Date*")
        with col2:
            u_tope = st.number_input("Último Tope (kg)", min_value=0.0, step=10.0)
            feed_kg = st.number_input("Feed Consumed / Week (kg)*", min_value=0.0, step=25.0)
        with col3:
            s_prod = st.selectbox("Feed Product Type*", ["(Select Feed)"] + list(product_mapping.keys()))
            act_w = st.number_input("Current Weight (g)*", min_value=0.0, step=0.1)
        with col4:
            st.text_area("Comments", height=120)
            if st.form_submit_button("Submit Log Update", type="primary"):
                
                # Check Auth permissions before allowing write to DB!
                if 'auth_user' in st.session_state and st.session_state.auth_user:
                    # Allow operation
                    if s_prod != "(Select Feed)":
                        cycle_id = int(active_cycles_df[active_cycles_df['pond_name'] == selected_pond].iloc[0]['cycle_id'])
                        try:
                            conn = get_connection()
                            with conn.cursor() as cur:
                                cur.execute("""INSERT INTO weekly_pond_logs (cycle_id, log_date, ultimo_tope_kg, feed_consumed_kg, product_id, actual_weight_g) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (cycle_id, log_date) DO UPDATE SET ultimo_tope_kg = EXCLUDED.ultimo_tope_kg, feed_consumed_kg = EXCLUDED.feed_consumed_kg, product_id = EXCLUDED.product_id, actual_weight_g = EXCLUDED.actual_weight_g;""", (cycle_id, log_dt, u_tope, feed_kg, product_mapping[s_prod], act_w))
                            conn.commit()
                            st.success("✅ Successfully logged data!")
                            st.cache_data.clear()
                        except Exception as e:
                            st.error(f"❌ Failed. {e}")
                else:
                    if not HAS_SUPABASE or not (SB_URL and SB_KEY):
                        # Running strictly securely in dev mode
                        st.warning("⚠️ Running in open dev mode. To secure data entry, deploy your Supabase Keys in .env")
                        if s_prod != "(Select Feed)":
                            cycle_id = int(active_cycles_df[active_cycles_df['pond_name'] == selected_pond].iloc[0]['cycle_id'])
                            try:
                                conn = get_connection()
                                with conn.cursor() as cur:
                                    cur.execute("""INSERT INTO weekly_pond_logs (cycle_id, log_date, ultimo_tope_kg, feed_consumed_kg, product_id, actual_weight_g) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (cycle_id, log_date) DO UPDATE SET ultimo_tope_kg = EXCLUDED.ultimo_tope_kg, feed_consumed_kg = EXCLUDED.feed_consumed_kg, product_id = EXCLUDED.product_id, actual_weight_g = EXCLUDED.actual_weight_g;""", (cycle_id, log_dt, u_tope, feed_kg, product_mapping[s_prod], act_w))
                                conn.commit()
                                st.success("✅ Successfully logged data in dev mode!")
                                st.cache_data.clear()
                            except Exception as e:
                                st.error(f"❌ Failed. {e}")
                    else:
                        st.error("Authentication required to submit data.")

with tab2:
    st.header("Empirical Growth Modeling")
    st.write("Generates individual biological curves for each pond.")
    growth_df = load_growth_curve_data()
    if HAS_SCIPY and not growth_df.empty:
        growth_df = growth_df[(growth_df['doc'] >= 0) & (growth_df['doc'] < 200) & (growth_df['actual_weight_g'] < 70)] 
        unique_ponds = sorted(growth_df['pond_name'].dropna().unique())
        color_map = {pond: px.colors.qualitative.Plotly[i % len(px.colors.qualitative.Plotly)] for i, pond in enumerate(unique_ponds)}
        fig2 = px.scatter(growth_df, x="doc", y="actual_weight_g", color="pond_name", color_discrete_map=color_map, opacity=0.3, symbol="point_type")
        
        if len(growth_df) > 5:
            def vbgf_gen(t, Winf, k, t0, b): return Winf * (np.maximum(0.0, 1.0 - np.exp(-k * (t - t0))) ** b)
            x_trend = np.linspace(-25, 150, 100)
            
            try:
                popt_global, _ = curve_fit(vbgf_gen, growth_df['doc'], growth_df['actual_weight_g'], p0=[65.0, 0.02, -21.0, 2.0], bounds=([55.0, 0.001, -35.0, 1.0], [90.0, 0.05, -10.0, 3.5]), maxfev=15000)
                sync_model_to_sql('Global Master', popt_global)
                fig2.add_scatter(x=x_trend, y=vbgf_gen(x_trend, *popt_global), mode='lines', name='GLOBAL MASTERLINE', line=dict(color='white', width=5, dash='dash'))
            except Exception: popt_global = None

            for pond in unique_ponds:
                p_df = growth_df[growth_df['pond_name'] == pond]
                if len(p_df) >= 4:
                    try:
                        p_popt, _ = curve_fit(vbgf_gen, p_df['doc'], p_df['actual_weight_g'], p0=[65.0, 0.02, -21.0, 2.0], bounds=([55.0, 0.001, -35.0, 1.0], [90.0, 0.05, -10.0, 3.5]), maxfev=10000)
                        sync_model_to_sql(f'Pond_{pond}', p_popt)
                        fig2.add_scatter(x=x_trend, y=vbgf_gen(x_trend, *popt_global), mode='lines', name=f'{pond}', line=dict(color=color_map[pond], width=2))
                    except Exception: pass
        fig2.update_layout(xaxis=dict(range=[-25, 150]), yaxis=dict(rangemode="tozero"))
        st.plotly_chart(fig2, use_container_width=True)

with tab3:
    st.header("🧪 Live Biomass & Survival Diagnostics")
    st.write("Visually align your theoretical mortality assumptions against factual harvests to retroactively discover true cycle survival.")
    
    cycle_df = load_cycle_dropdown()
    if not cycle_df.empty:
        col_c, col_v1, col_v2 = st.columns([2, 1, 1])
        with col_c: selected_cycle_display = st.selectbox("Select Growout Cycle to Diagnose", cycle_df['display_name'])
        
        with col_v1: transfer_shock = st.slider("Precría Transfer Shock Loss (%)", min_value=0.0, max_value=30.0, value=12.0, step=0.5)
        with col_v2: weekly_mort = st.slider("Basal Weekly Mortality (%)", min_value=0.0, max_value=5.0, value=1.5, step=0.1)
        
        cycle_id = int(cycle_df[cycle_df['display_name'] == selected_cycle_display]['cycle_id'].iloc[0])
        transfer, harvests, logs = get_cycle_diagnostic_data(cycle_id)
        
        if transfer.empty or len(logs) == 0:
            st.warning("Not enough Transfer and Weekly Log data exists for this cycle to build the computational timeline.")
        else:
            t_date = transfer['transfer_date'].iloc[0]
            t_qty = float(transfer['animals_transferred'].iloc[0])
            t_wt = float(transfer['transfer_weight_g'].iloc[0])
            
            # --- 1. Math: Form Weight Interpolation Timeline ---
            weight_points = [(0, t_wt)]
            for _, r in logs.iterrows(): weight_points.append(((r['log_date'] - t_date).days, r['actual_weight_g']))
            for _, r in harvests.iterrows():
                if pd.notnull(r['average_weight_g']) and r['average_weight_g'] > 0:
                    weight_points.append(((r['harvest_date'] - t_date).days, r['average_weight_g']))
            
            w_df = pd.DataFrame(weight_points, columns=['doc', 'weight']).dropna().sort_values('doc').groupby('doc').mean().reset_index()
            w_interp = interp1d(w_df['doc'], w_df['weight'], kind='linear', fill_value="extrapolate") if len(w_df) > 1 else lambda x: t_wt
            
            # --- 2. Run Time-Series Simulation ---
            if not harvests.empty: end_date = harvests['harvest_date'].max()
            else: end_date = date.today()
            
            daily_survival_factor = (1.0 - (weekly_mort / 100.0)) ** (1.0 / 7.0)
            
            curr_date = t_date
            curr_pop = t_qty * (1.0 - (transfer_shock / 100.0)) # Apply immediate day 0 shock
            
            sim_timeline = []
            while curr_date <= end_date:
                doc = (curr_date - t_date).days
                if doc > 0: curr_pop = curr_pop * daily_survival_factor
                
                # Deduct harvested animals instantly
                today_harvests = harvests[harvests['harvest_date'] == curr_date]
                for _, hr in today_harvests.iterrows():
                    h_lbs, h_wt = hr['total_lbs'], hr['average_weight_g']
                    if h_lbs > 0 and h_wt > 0:
                        animals_removed = (h_lbs * 453.592) / h_wt
                        curr_pop = max(0, curr_pop - animals_removed)
                
                est_wt = max(0.1, float(w_interp(doc)))
                biomass_lbs = (curr_pop * est_wt) / 453.592
                
                sim_timeline.append({
                    "Date": curr_date, "DOC": doc, "Population": curr_pop, "Biomass_Lbs": biomass_lbs,
                    "Survival_Pct": (curr_pop / t_qty) * 100.0
                })
                curr_date += timedelta(days=1)
                
            sim_df = pd.DataFrame(sim_timeline)
            
            # --- 3. Plotting the Diagnostics ---
            st.divider()
            fig3 = go.Figure()
            
            # Theoretical Biomass Curve
            fig3.add_trace(go.Scatter(x=sim_df['DOC'], y=sim_df['Biomass_Lbs'], mode='lines', 
                                      name='Theoretical In-Pond Biomass', line=dict(color='#00d4ff', width=3)))
            
            # Overlay Raleos & Harvests
            for _, hr in harvests.iterrows():
                doc_h = (hr['harvest_date'] - t_date).days
                h_type = str(hr['harvest_type']).upper()
                marker_color = 'red' if 'FINAL' in h_type or 'REPANO' in h_type else 'orange'
                fig3.add_trace(go.Scatter(
                    x=[doc_h], y=[hr['total_lbs']], mode='markers+text', 
                    name=f"Actual {h_type.capitalize()} Harvest", 
                    text=[f"{hr['total_lbs']:,.0f} Lbs [{h_type}]"], textposition="top center",
                    marker=dict(color=marker_color, size=15, symbol='star', line=dict(color='white', width=2))
                ))
            
            fig3.update_layout(title="Theoretical Biomass vs Final Ground Truth", xaxis_title="Days of Culture (DOC)", 
                               yaxis_title="Biomass (Lbs)", hovermode="x unified", yaxis=dict(rangemode="tozero"))
            
            st.plotly_chart(fig3, use_container_width=True)
            
            st.info("💡 **Tuning Guide:** Adjust the Mortality Sliders above. If the blue line ends up resting **exactly on top** of your Red Final Harvest Stars, your mortality estimate was physically flawless. If the star is lower than the line, they died faster in reality than you guessed.")
            
            # Populate Secondary Population Graph
            fig4 = px.line(sim_df, x="DOC", y="Population", title="Projected Animal Population Tracker (Factoring Raleos)")
            fig4.update_traces(line_color="#17B169")
            fig4.update_layout(yaxis=dict(rangemode="tozero"))
            st.plotly_chart(fig4, use_container_width=True)
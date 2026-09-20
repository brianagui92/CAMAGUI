import os
import traceback
import numpy as np
import pandas as pd
import psycopg2
import re
from psycopg2.extras import execute_values
from dotenv import load_dotenv
import argparse

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")


# --- DATA SANITIZATION HELPERS ---
def clean_val(val, val_type=float):
    if pd.isna(val) or val == "" or str(val).strip() in ("$ -", "-", "nan", "None"):
        return None
    cleaned = str(val).replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        if val_type == int:
            return int(round(float(cleaned)))
        return val_type(cleaned)
    except (ValueError, TypeError):
        return None


def clean_date(val):
    if pd.isna(val) or str(val).strip() in ("", "nan", "NaT", "None"):
        return None
    dt = pd.to_datetime(val, dayfirst=True, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.date()


# --- STEP 1: MIGRATE ORG, FARM, AND PONDS ---
def migrate_spreadsheet(file_path, org_name="CAMAGUI", farm_name="CAMAGUI"):
    try:
        print(f"\n[1/3] Reading PISCINAS sheet from {file_path}...")
        df_piscinas = pd.read_excel(file_path, sheet_name="PISCINAS")

        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                # 1. Organization
                cur.execute("""
                            INSERT INTO organizations (name)
                            VALUES (%s)
                            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                            RETURNING org_id;
                            """, (org_name,))
                org_id = cur.fetchone()[0]

                # 2. Farm
                cur.execute("""
                            INSERT INTO farms (org_id, name)
                            VALUES (%s, %s)
                            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                            RETURNING farm_id;
                            """, (org_id, farm_name))
                farm_id = cur.fetchone()[0]

                # 3. Ponds
                print("Migrating ponds...")
                for _, row in df_piscinas.iterrows():
                    raw_pond = row.get("PISCINA")
                    if pd.isna(raw_pond):
                        continue
                    pond_name = str(raw_pond).split(".")[0].strip()
                    hectares = clean_val(row.get("HECTAREA"), float) or 0.0

                    # Use ON CONFLICT if we add a unique constraint, otherwise standard SELECT/INSERT
                    cur.execute("SELECT pond_id FROM ponds WHERE farm_id = %s AND pond_name = %s;",
                                (farm_id, pond_name))
                    row_pond = cur.fetchone()

                    if not row_pond:
                        cur.execute("""
                                    INSERT INTO ponds (farm_id, pond_name, hectares)
                                    VALUES (%s, %s, %s);
                                    """, (farm_id, pond_name, hectares))
                    else:
                        cur.execute("UPDATE ponds SET hectares = %s WHERE pond_id = %s;", (hectares, row_pond[0]))

        print("✓ Farm, organization, and ponds migrated successfully.")

    except Exception as e:
        print(f"Error during farm/pond migration: {e}")
        traceback.print_exc()
        raise e


# --- STEP 2: MIGRATE PRODUCTS AND PRICES ---
def migrate_products_and_prices(file_path):
    try:
        print(f"\n[2/3] Reading PRECIOS sheet from {file_path}...")
        df_prices = pd.read_excel(file_path, sheet_name="PRECIOS").dropna(subset=["INSUMOS"])

        # Clean headers to ensure "PRECIO" is found accurately
        df_prices.columns = [re.sub(r'\s+', ' ', str(col)).strip() for col in df_prices.columns]

        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                baseline_date = "2022-01-01"
                print("Migrating products and initial prices...")

                for _, row in df_prices.iterrows():
                    product_name = str(row["INSUMOS"]).strip()

                    # Capture the short code (Fallback to the name if CODIGO is empty)
                    raw_code = row.get("CODIGO")
                    product_code = str(raw_code).strip() if pd.notna(raw_code) else product_name

                    tamano_kg = clean_val(row.get("TAMANO KG"), float) or 25.0
                    package_price = clean_val(row.get("PRECIO"), float) or 0.0

                    name_lower = product_name.lower()
                    if any(x in name_lower for x in ["balanceado", "skretting", "nicovita"]):
                        category = "feed"
                    else:
                        category = "insumo"

                    # Upsert Product using product_code for the conflict
                    cur.execute("""
                                INSERT INTO products (name, product_code, category, base_unit, package_weight_kg)
                                VALUES (%s, %s, %s, 'kg', %s)
                                ON CONFLICT (product_code) DO UPDATE SET name              = EXCLUDED.name,
                                                                         category          = EXCLUDED.category,
                                                                         package_weight_kg = EXCLUDED.package_weight_kg
                                RETURNING product_id;
                                """, (product_name, product_code, category, tamano_kg))
                    product_id = cur.fetchone()[0]

                    # Insert Price Timeline
                    cur.execute("SELECT price_id FROM product_prices WHERE product_id = %s AND effective_date = %s;",
                                (product_id, baseline_date))
                    if not cur.fetchone():
                        cur.execute("""
                                    INSERT INTO product_prices (product_id, package_price, effective_date)
                                    VALUES (%s, %s, %s);
                                    """, (product_id, package_price, baseline_date))

        print("✓ Products and price timeline migrated successfully.")

    except Exception as e:
        print(f"Error during price migration: {e}")
        traceback.print_exc()
        raise e



# --- STEP 3: MIGRATE PRECRIA AND SIEMBRAS (RAW FACTS ONLY) ---
def migrate_precria_and_siembras(file_path, farm_name="CAMAGUI"):
    try:
        print(f"\n[3/3] Reading PRECRIA and SIEMBRA sheets from {file_path}...")
        df_precria = pd.read_excel(file_path, sheet_name="PRECRIA CAMAGUI")
        df_siembra = pd.read_excel(file_path, sheet_name="SIEMB. CAMAGUI")

        # Strip all invisible newlines (Alt+Enter) and double spaces from Excel headers
        df_precria.columns = [re.sub(r'\s+', ' ', str(col)).strip() for col in df_precria.columns]
        df_siembra.columns = [re.sub(r'\s+', ' ', str(col)).strip() for col in df_siembra.columns]

        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:

                # Fetch Farm ID
                cur.execute("SELECT farm_id FROM farms WHERE name = %s;", (farm_name,))
                farm_row = cur.fetchone()
                if not farm_row:
                    raise ValueError(f"Farm '{farm_name}' not found. Run Step 1 first.")
                farm_id = farm_row[0]

                # Cache Ponds
                cur.execute("SELECT pond_name, pond_id FROM ponds WHERE farm_id = %s;", (farm_id,))
                pond_map = {str(row[0]).strip(): row[1] for row in cur.fetchall()}

                def get_or_create_pond(p_name):
                    p_name_clean = str(p_name).split(".")[0].strip() if pd.notna(p_name) else "0"
                    if p_name_clean not in pond_map:
                        print(f"  [!] Auto-creating missing pond: {p_name_clean}")
                        cur.execute("""
                                    INSERT INTO ponds (farm_id, pond_name, hectares)
                                    VALUES (%s, %s, 0.0)
                                    RETURNING pond_id;
                                    """, (farm_id, p_name_clean))
                        pond_map[p_name_clean] = cur.fetchone()[0]
                    return pond_map[p_name_clean]

                # -------------------------------------------------------------
                # 3A. Precría Batches & Feeds
                # -------------------------------------------------------------
                print("Ingesting precria batches and feed logs...")
                batch_id_map = {}

                for _, row in df_precria.iterrows():
                    raw_batch = row.get("PRECRIA ID#")
                    if pd.isna(raw_batch) or not str(raw_batch).strip():
                        continue
                    batch_code = str(raw_batch).strip()

                    stocked_animals = clean_val(row.get("ANIMALES SEMBRADOS"), int) or 0
                    stocking_date = clean_date(row.get("FECHA SIEMB."))

                    # Upsert Batch
                    cur.execute("""
                                INSERT INTO precria_batches (farm_id, batch_code, start_date, initial_animals)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (batch_code) DO UPDATE SET start_date      = EXCLUDED.start_date,
                                                                       initial_animals = EXCLUDED.initial_animals
                                RETURNING precria_batch_id;
                                """, (farm_id, batch_code, stocking_date, stocked_animals))

                    p_batch_id = cur.fetchone()[0]
                    batch_id_map[batch_code] = p_batch_id

                    # Restore your original feed application loop (Raw facts)
                    cur.execute("DELETE FROM precria_feed_applications WHERE precria_batch_id = %s;", (p_batch_id,))
                    for idx in range(1, 5):
                        kg = clean_val(row.get(f"BALANCEADO #{idx} (Kg)"))
                        feed_name = row.get(f"NOMBRE BALANC. #{idx}")
                        cost = clean_val(row.get(f"GASTO BALANC. #{idx}"))

                        if kg and kg > 0:
                            raw_name = str(feed_name).strip() if pd.notna(feed_name) and str(
                                feed_name).strip() else "Desconocido"
                            cur.execute("""
                                        INSERT INTO precria_feed_applications (precria_batch_id, raw_product_name, quantity_kg, cost)
                                        VALUES (%s, %s, %s, %s);
                                        """, (p_batch_id, raw_name, kg, cost or 0.0))

                # -------------------------------------------------------------
                # 3B. Growout Cycles
                # -------------------------------------------------------------
                print("Ingesting growout cycles (siembras)...")
                cycle_id_map = {}

                for _, row in df_siembra.iterrows():
                    raw_cycle = row.get("SIEMBRA ID#")
                    if pd.isna(raw_cycle) or not str(raw_cycle).strip():
                        continue
                    cycle_code = str(raw_cycle).strip()

                    pond_id = get_or_create_pond(row.get("PISCINA"))
                    stocking_date = clean_date(row.get("FECHA SIEMB."))
                    init_weight = clean_val(row.get("PESO DE SIEMBRA"), float)

                    cur.execute("""
                                INSERT INTO growout_cycles (cycle_code, farm_id, pond_id, stocking_date, initial_weight_g)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (cycle_code) DO UPDATE SET stocking_date    = EXCLUDED.stocking_date,
                                                                       initial_weight_g = EXCLUDED.initial_weight_g
                                RETURNING cycle_id;
                                """, (cycle_code, farm_id, pond_id, stocking_date, init_weight))

                    cycle_id_map[cycle_code] = cur.fetchone()[0]

                # -------------------------------------------------------------
                # 3C. Precría Transfers (Raw Physical Facts)
                # -------------------------------------------------------------
                print("Linking precria-to-siembra transfers...")
                for _, row in df_siembra.iterrows():
                    cycle_code = str(row.get("SIEMBRA ID#", "")).strip()
                    precria_code = str(row.get("PRECRIA ID#", "")).strip()

                    c_id = cycle_id_map.get(cycle_code)
                    b_id = batch_id_map.get(precria_code)

                    if c_id and b_id:
                        animals = clean_val(row.get("ANIMALES"), int) or 0
                        t_date = clean_date(row.get("FECHA SIEMB."))
                        transfer_weight = clean_val(row.get("PESO DE SIEMBRA"), float)

                        cur.execute("""
                                    INSERT INTO precria_transfers (precria_batch_id, cycle_id, transfer_date,
                                                                   animals_transferred, transfer_weight_g)
                                    VALUES (%s, %s, %s, %s, %s)
                                    ON CONFLICT (precria_batch_id, cycle_id) DO UPDATE SET transfer_date       = EXCLUDED.transfer_date,
                                                                                           animals_transferred = EXCLUDED.animals_transferred,
                                                                                           transfer_weight_g   = EXCLUDED.transfer_weight_g;
                                    """, (b_id, c_id, t_date, animals, transfer_weight))

            conn.commit()

        print("✓ Precrías, Siembras, and Transfers linked successfully.")

    except Exception as e:
        print(f"Error during linked migration: {e}")
        traceback.print_exc()
        raise e



# --- STEP 4: MIGRATE FEEDING CURVES ---
def migrate_feeding_curves(file_path):
    try:
        print(f"\n[*] Reading feeding curves from {file_path}...")
        df_curves = pd.read_excel(file_path, sheet_name="Masterline")
        df_curves.columns = [re.sub(r'\s+', ' ', str(col)).strip() for col in df_curves.columns]

        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                print("Ingesting smoothed Masterline waypoints...")

                prev_bw_min = None
                last_max_weight = None
                last_bw_min = None

                for _, row in df_curves.iterrows():
                    waypoint_g = clean_val(row.get("PESO MIN (g)"), float)

                    if waypoint_g is None:
                        continue

                    current_bw_min = clean_val(row.get("%BW Min"), float)
                    current_bw_max = clean_val(row.get("%BW Max"), float)
                    current_max_weight = clean_val(row.get("PESO MAX (g)"), float)

                    if current_bw_min is None or current_bw_max is None:
                        continue

                    # Calculate smoothed target percentage
                    if prev_bw_min is None:
                        # First row logic
                        target_pct = round((current_bw_min + current_bw_max) / 2.0, 3)
                    else:
                        # Smoothed biological decay logic
                        target_pct = round((current_bw_max + prev_bw_min) / 2.0, 3)

                    # Store variables for the next loop... and for the final anchor
                    prev_bw_min = current_bw_min
                    last_max_weight = current_max_weight
                    last_bw_min = current_bw_min

                    # Insert standard waypoint
                    cur.execute("""
                                INSERT INTO feeding_curves (curve_name, weight_waypoint_g, target_bw_pct)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (curve_name, weight_waypoint_g) DO UPDATE SET target_bw_pct = EXCLUDED.target_bw_pct;
                                """, ("Masterline", waypoint_g, target_pct))

                # --- The Final Anchor (Closing the curve) ---
                if last_max_weight is not None and last_bw_min is not None:
                    # Rounding 30.99 to exactly 31 as requested
                    final_waypoint_g = round(last_max_weight)

                    cur.execute("""
                                INSERT INTO feeding_curves (curve_name, weight_waypoint_g, target_bw_pct)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (curve_name, weight_waypoint_g) DO UPDATE SET target_bw_pct = EXCLUDED.target_bw_pct;
                                """, ("Masterline", final_waypoint_g, last_bw_min))

                    print(f"  -> Added terminal anchor: {final_waypoint_g}g at {last_bw_min}%")

        print("✓ Smoothed Masterline feeding curve ingested successfully.")

    except Exception as e:
        print(f"Error during feeding curve migration: {e}")
        traceback.print_exc()
        raise e


# --- STEP 5: MIGRATE WEEKLY POND LOGS (RAW INPUTS ONLY) ---
def migrate_weekly_logs(file_path):
    try:
        print(f"\n[*] Reading core weekly logs from {file_path}...")
        df_logs = pd.read_excel(file_path, sheet_name="CAMAGUI DATA UNIFICADA")
        df_logs.columns = [re.sub(r'\s+', ' ', str(col)).strip() for col in df_logs.columns]

        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                print("Ingesting raw facts from CAMAGUI DATA UNIFICADA...")

                # Cache Cycles
                cur.execute("SELECT cycle_code, cycle_id FROM growout_cycles;")
                cycle_map = {str(row[0]).strip(): row[1] for row in cur.fetchall()}

                # Cache Products by CODE instead of name
                cur.execute("SELECT LOWER(TRIM(product_code)), product_id FROM products;")
                product_map = {row[0]: row[1] for row in cur.fetchall()}

                def get_or_create_product(raw_feed_name):
                    if pd.isna(raw_feed_name) or not str(raw_feed_name).strip():
                        return None

                    clean_code = str(raw_feed_name).strip()
                    lookup_code = clean_code.lower()

                    if lookup_code not in product_map:
                        print(f"  [!] Auto-creating missing feed product by code: '{clean_code}'")
                        # If auto-created, we just set the name and code to be identical
                        cur.execute("""
                                    INSERT INTO products (name, product_code, category, base_unit, package_weight_kg)
                                    VALUES (%s, %s, 'feed', 'kg', 25.0)
                                    RETURNING product_id;
                                    """, (clean_code, clean_code))
                        product_map[lookup_code] = cur.fetchone()[0]

                    return product_map[lookup_code]

                for _, row in df_logs.iterrows():
                    raw_cycle = row.get("SIEMBRA ID #")
                    if pd.isna(raw_cycle) or not str(raw_cycle).strip():
                        continue

                    cycle_code = str(raw_cycle).strip()
                    cycle_id = cycle_map.get(cycle_code)

                    if not cycle_id:
                        continue

                    log_date = clean_date(row.get("FECHA"))
                    if not log_date:
                        continue

                    # The 4 Core Realities
                    ultimo_tope = clean_val(row.get("ULTIMO TOPE (Kg)"))
                    consumo = clean_val(row.get("CONSUMO SEMANA (Kg)"))
                    peso_actual = clean_val(row.get("PESO ACTUAL"))

                    # Relational Product Lookup
                    product_id = get_or_create_product(row.get("NOMBRE BALANCEAD."))

                    cur.execute("""
                                INSERT INTO weekly_pond_logs (cycle_id, log_date, ultimo_tope_kg, feed_consumed_kg,
                                                              product_id, actual_weight_g)
                                VALUES (%s, %s, %s, %s, %s, %s)
                                ON CONFLICT (cycle_id, log_date) DO UPDATE SET ultimo_tope_kg   = EXCLUDED.ultimo_tope_kg,
                                                                               feed_consumed_kg = EXCLUDED.feed_consumed_kg,
                                                                               product_id       = EXCLUDED.product_id,
                                                                               actual_weight_g  = EXCLUDED.actual_weight_g;
                                """, (cycle_id, log_date, ultimo_tope, consumo, product_id, peso_actual))

        print("✓ Raw weekly pond logs migrated successfully.")

    except Exception as e:
        print(f"Error during weekly log migration: {e}")
        traceback.print_exc()
        raise e


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CAMAGUI Database Migration CLI Tool")
    parser.add_argument("-f", "--file", type=str, default="Calculos_Camaronera.xlsx",
                        help="Path to the Excel file (default: Calculos_Camaronera.xlsx)")
    parser.add_argument("--ponds", action="store_true",
                        help="Migrate Organization, Farm, and Ponds")
    parser.add_argument("--prices", action="store_true",
                        help="Migrate Products and Prices")
    parser.add_argument("--cycles", action="store_true",
                        help="Migrate Precrías, Siembras, and Transfers")
    parser.add_argument("--curves", action="store_true",
                        help="Migrate Masterline feeding curves")
    parser.add_argument("--curvename", type=str, default="Masterline",
                        help="Name to assign to the feeding curve (default: 'Masterline')")
    parser.add_argument("--logs", action="store_true",
                        help="Migrate raw weekly pond logs (Inputs only)")
    parser.add_argument("--all", action="store_true",
                        help="Run ALL migration steps sequentially")

    args = parser.parse_args()

    # If no flags are passed, show the help menu automatically
    if not any([args.ponds, args.prices, args.cycles, args.curves, args.logs, args.all]):
        parser.print_help()
    else:
        if args.all or args.ponds:
            migrate_spreadsheet(args.file, org_name="CAMAGUI", farm_name="CAMAGUI")

        if args.all or args.prices:
            migrate_products_and_prices(args.file)

        if args.all or args.cycles:
            migrate_precria_and_siembras(args.file, farm_name="CAMAGUI")

        if args.all or args.curves:
            migrate_feeding_curves(args.file)

        if args.all or args.logs:
            migrate_weekly_logs(args.file)
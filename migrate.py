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

        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                baseline_date = "2022-01-01"
                print("Migrating products and initial prices...")

                for _, row in df_prices.iterrows():
                    product_name = str(row["INSUMOS"]).strip()
                    tamano_kg = clean_val(row.get("TAMANO KG"), float) or 25.0
                    precio_kg = clean_val(row.get("PRECIO/KG"), float) or 0.0

                    name_lower = product_name.lower()
                    if any(x in name_lower for x in ["balanceado", "skretting", "nicovita"]):
                        category = "feed"
                    else:
                        category = "insumo"

                    # Upsert Product
                    cur.execute("""
                                INSERT INTO products (name, category, base_unit, package_weight_kg)
                                VALUES (%s, %s, 'kg', %s)
                                ON CONFLICT (name) DO UPDATE SET category          = EXCLUDED.category,
                                                                 package_weight_kg = EXCLUDED.package_weight_kg
                                RETURNING product_id;
                                """, (product_name, category, tamano_kg))
                    product_id = cur.fetchone()[0]

                    # Insert Price Timeline (Avoid Duplicates)
                    cur.execute("SELECT price_id FROM product_prices WHERE product_id = %s AND effective_date = %s;",
                                (product_id, baseline_date))
                    if not cur.fetchone():
                        cur.execute("""
                                    INSERT INTO product_prices (product_id, unit_price, effective_date)
                                    VALUES (%s, %s, %s);
                                    """, (product_id, precio_kg, baseline_date))

        print("✓ Products and price timeline migrated successfully.")

    except Exception as e:
        print(f"Error during price migration: {e}")
        traceback.print_exc()
        raise e


# --- STEP 3: MIGRATE PRECRIA AND SIEMBRAS ---
def migrate_precria_and_siembras(file_path, farm_name="CAMAGUI"):
    try:
        print(f"\n[3/3] Reading PRECRIA and SIEMBRA sheets from {file_path}...")
        df_precria = pd.read_excel(file_path, sheet_name="PRECRIA CAMAGUI")
        df_siembra = pd.read_excel(file_path, sheet_name="SIEMB. CAMAGUI")

        # THE MAGIC FIX: Strip all invisible newlines (Alt+Enter) and double spaces from Excel headers
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

                    pond_id = get_or_create_pond(row.get("PISC"))

                    stocked_animals = clean_val(row.get("ANIMALES SEMBRADOS"), int) or 0
                    stocking_date = clean_date(row.get("FECHA SIEMB."))
                    transfer_date = clean_date(row.get("FECHA COSE."))
                    days = clean_val(row.get("DIAS"), int)
                    final_weight = clean_val(row.get("GRAM"), float)

                    # Upsert Batch
                    cur.execute("""
                                INSERT INTO precria_batches (batch_code, farm_id, pond_id, stocked_animals,
                                                             stocking_date, transfer_date, days, final_weight_g)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (batch_code) DO UPDATE SET stocked_animals = EXCLUDED.stocked_animals,
                                                                       stocking_date   = EXCLUDED.stocking_date,
                                                                       transfer_date   = EXCLUDED.transfer_date,
                                                                       days            = EXCLUDED.days,
                                                                       final_weight_g  = EXCLUDED.final_weight_g
                                RETURNING precria_batch_id;
                                """, (batch_code, farm_id, pond_id, stocked_animals, stocking_date, transfer_date, days,
                                      final_weight))
                    p_batch_id = cur.fetchone()[0]
                    batch_id_map[batch_code] = p_batch_id

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
                # 3C. Precría Transfers
                # -------------------------------------------------------------
                print("Linking precria-to-siembra transfers...")
                for _, row in df_siembra.iterrows():
                    cycle_code = str(row.get("SIEMBRA ID#", "")).strip()
                    precria_code = str(row.get("PRECRIA ID#", "")).strip()

                    c_id = cycle_id_map.get(cycle_code)
                    b_id = batch_id_map.get(precria_code)

                    if c_id and b_id:
                        animals = clean_val(row.get("ANIMALES"), int) or 0
                        prorated_kg = clean_val(row.get("PRECRIA BALANCEADO"), float)
                        prorated_cost = clean_val(row.get("GASTO BALAN."), float)
                        t_date = clean_date(row.get("FECHA SIEMB."))

                        cur.execute("""
                                    INSERT INTO precria_transfers (precria_batch_id, cycle_id, transferred_animals,
                                                                   prorated_feed_kg, prorated_feed_cost, transfer_date)
                                    VALUES (%s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (precria_batch_id, cycle_id) DO UPDATE SET transferred_animals = EXCLUDED.transferred_animals,
                                                                                           prorated_feed_kg    = EXCLUDED.prorated_feed_kg,
                                                                                           prorated_feed_cost  = EXCLUDED.prorated_feed_cost;
                                    """, (b_id, c_id, animals, prorated_kg, prorated_cost, t_date))

            conn.commit()

        print("✓ Precrías, Siembras, and Transfers linked successfully.")

    except Exception as e:
        print(f"Error during linked migration: {e}")
        traceback.print_exc()
        raise e




if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CAMAGUI Database Migration CLI Tool")
    parser.add_argument("-f", "--file", type=str, default="Calculos_Camaronera.xlsx",
                        help="Path to the Excel file (default: Calculos_Camaronera.xlsx)")
    parser.add_argument("--ponds", action="store_true",
                        help="Migrate Organization, Farm, and Ponds")
    parser.add_argument("--prices", action="store_true",
                        help="Migrate Products and Prices")
    parser.add_argument("--cycles", action="store_true",
                        help="Migrate Precrías, Siembras, and Transfers")
    parser.add_argument("--all", action="store_true",
                        help="Run ALL migration steps sequentially")

    args = parser.parse_args()

    # If no arguments are passed, show the help menu
    if not (args.ponds or args.prices or args.cycles or args.all):
        parser.print_help()
    else:
        if args.all or args.ponds:
            migrate_spreadsheet(args.file, org_name="CAMAGUI", farm_name="CAMAGUI")

        if args.all or args.prices:
            migrate_products_and_prices(args.file)

        if args.all or args.cycles:
            migrate_precria_and_siembras(args.file, farm_name="CAMAGUI")
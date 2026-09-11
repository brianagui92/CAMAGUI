import os
import psycopg2
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")


def migrate_spreadsheet(file_path, org_name):
    try:
        print(f"Reading spreadsheet: {file_path}...")
        df = pd.read_excel(file_path, sheet_name="PISCINAS")

        print("Connecting to Supabase for migration...")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        print(f"Inserting organization '{org_name}'...")
        cur.execute("""
                    INSERT INTO organizations (name)
                    VALUES (%s)
                    ON CONFLICT ON CONSTRAINT unique_org_name DO NOTHING
                    RETURNING org_id;
                    """, (org_name,))

        result = cur.fetchone()
        if result:
            org_id = result[0]
        else:
            cur.execute("SELECT org_id FROM organizations WHERE name = %s;", (org_name,))
            org_id = cur.fetchone()[0]

        print("Migrating farms and ponds...")
        for _, row in df.iterrows():
            farm_name = str(row['CAMARONERA']).strip()
            pond_name = str(row['PISCINA']).strip()
            hectares = float(row['HECTAREA'])

            # Insert Farm
            cur.execute("""
                        INSERT INTO farms (org_id, name)
                        VALUES (%s, %s)
                        ON CONFLICT ON CONSTRAINT unique_farm_name DO NOTHING
                        RETURNING farm_id;
                        """, (org_id, farm_name))

            # Fetch farm_id
            cur.execute("""
                        SELECT farm_id
                        FROM farms
                        WHERE org_id = %s
                          AND name = %s;
                        """, (org_id, farm_name))
            farm_id = cur.fetchone()[0]

            # Insert Pond
            cur.execute("""
                        INSERT INTO ponds (farm_id, pond_name, hectares)
                        VALUES (%s, %s, %s);
                        """, (farm_id, pond_name, hectares))



        conn.commit()
        cur.close()
        conn.close()
        print(f"Migration complete for {file_path} under '{org_name}'.")

    except Exception as e:
        print(f"Error during migration: {e}")

#migrate the prices from the excel
def migrate_products_and_prices(file_path):
    try:
        print(f"Reading PRECIOS sheet from: {file_path}...")
        df_prices = pd.read_excel(file_path, sheet_name="PRECIOS").dropna(subset=['INSUMOS'])

        print("Connecting to Supabase to migrate prices...")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        baseline_date = '2022-01-01'

        print("Migrating products with Bodega base-unit structure...")
        for _, row in df_prices.iterrows():
            product_name = str(row['INSUMOS']).strip()

            # Extract values
            tamano_kg = float(row['TAMANO KG'])
            precio_kg = float(row['PRECIO/KG'])

            # Categorize feed vs insumos
            name_lower = product_name.lower()
            if "balanceado" in name_lower or "skretting" in name_lower or "nicovita" in name_lower:
                category = "feed"
            else:
                category = "insumo"

            # 1. Insert Product (Ready for Bodega logic)
            cur.execute("""
                        INSERT INTO products (name, category, base_unit, package_weight_kg)
                        VALUES (%s, %s, 'kg', %s)
                        ON CONFLICT ON CONSTRAINT unique_product_name DO NOTHING
                        RETURNING product_id;
                        """, (product_name, category, tamano_kg))

            result = cur.fetchone()
            if result:
                product_id = result[0]
            else:
                cur.execute("SELECT product_id FROM products WHERE name = %s;", (product_name,))
                product_id = cur.fetchone()[0]

            # 2. Insert the Price (We store the per-KG price as the ultimate source of truth)
            cur.execute("""
                        SELECT price_id
                        FROM product_prices
                        WHERE product_id = %s
                          AND effective_date = %s;
                        """, (product_id, baseline_date))

            if not cur.fetchone():
                cur.execute("""
                            INSERT INTO product_prices (product_id, unit_price, effective_date)
                            VALUES (%s, %s, %s);
                            """, (product_id, precio_kg, baseline_date))

        conn.commit()
        cur.close()
        conn.close()
        print(f"Price migration complete! Bodega-ready structure established.")

    except Exception as e:
        print(f"Error during price migration: {e}")


if __name__ == "__main__":
    # 1. Migrate the camaroneras and ponds
    migrate_spreadsheet("Calculos_Camaronera.xlsx", org_name="CAMAGUI")

    # 2. Migrate the products and initial prices
    migrate_products_and_prices("Calculos_Camaronera.xlsx")

    # Later on, when you want to ingest your second file, you can easily add:
    # migrate_spreadsheet("Calculos_Camaronera_Sociedad.xlsx", org_name="New Farm Entity")
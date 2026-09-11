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


if __name__ == "__main__":
    # Runs the migration for your main spreadsheet file
    migrate_spreadsheet("Calculos_Camaronera.xlsx", org_name="CAMAGUI")

    # Later on, when you want to ingest your second file, you can easily add:
    # migrate_spreadsheet("Calculos_Camaronera_Sociedad.xlsx", org_name="New Farm Entity")
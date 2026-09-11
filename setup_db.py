import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")


def create_tables():
    try:
        print("Connecting to Supabase for database setup...")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        # 1. Drop existing tables to ensure a clean slate
        # Note: We must drop tables that have foreign keys first (prices depend on products)
        print("Dropping old tables...")
        cur.execute("""
                    DROP TABLE IF EXISTS product_prices CASCADE;
                    DROP TABLE IF EXISTS products CASCADE;
                    DROP TABLE IF EXISTS ponds CASCADE;
                    DROP TABLE IF EXISTS farms CASCADE;
                    DROP TABLE IF EXISTS organizations CASCADE;
                    """)

        # 2. Create the core organizational tables
        print("Creating core structure (organizations, farms, ponds)...")

        cur.execute("""
                    CREATE TABLE organizations
                    (
                        org_id     SERIAL PRIMARY KEY,
                        name       VARCHAR(150) NOT NULL,
                        CONSTRAINT unique_org_name UNIQUE (name),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)

        cur.execute("""
                    CREATE TABLE farms
                    (
                        farm_id    SERIAL PRIMARY KEY,
                        org_id     INT          NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
                        name       VARCHAR(150) NOT NULL,
                        location   VARCHAR(100),
                        CONSTRAINT unique_farm_name UNIQUE (name),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)

        cur.execute("""
                    CREATE TABLE ponds
                    (
                        pond_id    SERIAL PRIMARY KEY,
                        farm_id    INT           NOT NULL REFERENCES farms (farm_id) ON DELETE CASCADE,
                        pond_name  VARCHAR(50)   NOT NULL,
                        hectares   NUMERIC(6, 2) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)

        # 3. Create the inventory and pricing tables
        print("Creating inventory and pricing tables...")

        cur.execute("""
                    CREATE TABLE products (
                        product_id SERIAL PRIMARY KEY,
                        name VARCHAR(150) NOT NULL,
                        category VARCHAR(50), 
                        base_unit VARCHAR(20) DEFAULT 'kg', 
                        package_weight_kg NUMERIC(6, 2) NOT NULL, -- Pulls from TAMANO KG
                        CONSTRAINT unique_product_name UNIQUE (name),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)

        cur.execute("""
                    CREATE TABLE product_prices
                    (
                        price_id       SERIAL PRIMARY KEY,
                        product_id     INT            NOT NULL REFERENCES products (product_id) ON DELETE CASCADE,
                        unit_price     NUMERIC(10, 4) NOT NULL,
                        effective_date DATE           NOT NULL,
                        end_date       DATE,
                        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)

        conn.commit()
        cur.close()
        conn.close()
        print("Database tables created successfully!")

    except Exception as e:
        print(f"Error during database setup: {e}")


if __name__ == "__main__":
    create_tables()
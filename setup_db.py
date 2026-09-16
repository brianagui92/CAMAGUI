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

        print("Dropping old tables in dependency order...")
        cur.execute("""
            DROP TABLE IF EXISTS weekly_pond_logs CASCADE;
            DROP TABLE IF EXISTS growth_curves CASCADE;
            DROP TABLE IF EXISTS feeding_curves CASCADE;
            DROP TABLE IF EXISTS precria_transfers CASCADE;
            DROP TABLE IF EXISTS precria_feed_applications CASCADE;
            DROP TABLE IF EXISTS precria_batches CASCADE;
            DROP TABLE IF EXISTS larvae_providers CASCADE;
            DROP TABLE IF EXISTS growout_cycles CASCADE;
            DROP TABLE IF EXISTS product_prices CASCADE;
            DROP TABLE IF EXISTS products CASCADE;
            DROP TABLE IF EXISTS ponds CASCADE;
            DROP TABLE IF EXISTS farms CASCADE;
            DROP TABLE IF EXISTS organizations CASCADE;
        """)

        print("Creating core enterprise tables...")
        cur.execute("""
            CREATE TABLE organizations (
                org_id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE farms (
                farm_id SERIAL PRIMARY KEY,
                org_id INT NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
                name VARCHAR(150) NOT NULL UNIQUE,
                location VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE ponds (
                pond_id SERIAL PRIMARY KEY,
                farm_id INT NOT NULL REFERENCES farms(farm_id) ON DELETE CASCADE,
                pond_name VARCHAR(50) NOT NULL,
                hectares NUMERIC(6, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        print("Creating products, prices, and curves...")
        cur.execute("""
            CREATE TABLE products (
                product_id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL UNIQUE,
                category VARCHAR(50), 
                base_unit VARCHAR(20) DEFAULT 'kg', 
                package_weight_kg NUMERIC(6, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE product_prices (
                price_id SERIAL PRIMARY KEY,
                product_id INT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                unit_price NUMERIC(10, 4) NOT NULL,
                effective_date DATE NOT NULL,
                end_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE feeding_curves (
                curve_id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                min_weight_g NUMERIC(5, 2) NOT NULL,
                max_weight_g NUMERIC(5, 2) NOT NULL,
                feeding_rate_pct NUMERIC(5, 3) NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE growth_curves (
                growth_curve_id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                min_weight_g NUMERIC(5, 2) NOT NULL,
                max_weight_g NUMERIC(5, 2) NOT NULL,
                expected_weekly_gain_g NUMERIC(5, 2) NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        print("Creating nursery (precría) and growout tracking tables...")
        cur.execute("""
            CREATE TABLE larvae_providers (
                provider_id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL UNIQUE,
                contact_info VARCHAR(200),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE precria_batches (
                precria_batch_id SERIAL PRIMARY KEY,
                batch_code VARCHAR(50) NOT NULL UNIQUE,
                farm_id INT NOT NULL REFERENCES farms(farm_id) ON DELETE CASCADE,
                pond_id INT NOT NULL REFERENCES ponds(pond_id) ON DELETE CASCADE,
                provider_id INT REFERENCES larvae_providers(provider_id) ON DELETE SET NULL,
                initial_pl_age INT,
                pl_per_gram NUMERIC(8, 2),
                stocked_animals INT NOT NULL,
                stocking_date DATE NOT NULL,
                transfer_date DATE,
                days INT,
                final_weight_g NUMERIC(5, 2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE precria_feed_applications (
                application_id SERIAL PRIMARY KEY,
                precria_batch_id INT NOT NULL REFERENCES precria_batches(precria_batch_id) ON DELETE CASCADE,
                product_id INT REFERENCES products(product_id),
                raw_product_name VARCHAR(150),
                quantity_kg NUMERIC(10, 2) NOT NULL,
                cost NUMERIC(10, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE growout_cycles (
                cycle_id SERIAL PRIMARY KEY,
                cycle_code VARCHAR(50) NOT NULL UNIQUE,
                farm_id INT NOT NULL REFERENCES farms(farm_id) ON DELETE CASCADE,
                pond_id INT NOT NULL REFERENCES ponds(pond_id) ON DELETE CASCADE,
                stocking_date DATE NOT NULL,
                initial_weight_g NUMERIC(5, 2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE precria_transfers (
                transfer_id SERIAL PRIMARY KEY,
                precria_batch_id INT NOT NULL REFERENCES precria_batches(precria_batch_id) ON DELETE CASCADE,
                cycle_id INT NOT NULL REFERENCES growout_cycles(cycle_id) ON DELETE CASCADE,
                transferred_animals INT NOT NULL,
                prorated_feed_kg NUMERIC(10, 2),
                prorated_feed_cost NUMERIC(10, 2),
                transfer_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_batch_cycle_transfer UNIQUE (precria_batch_id, cycle_id)
            );
        """)

        print("Creating weekly logs table...")
        cur.execute("""
            CREATE TABLE weekly_pond_logs (
                log_id SERIAL PRIMARY KEY,
                cycle_id INT NOT NULL REFERENCES growout_cycles(cycle_id) ON DELETE CASCADE,
                log_date DATE NOT NULL,

                feed_product_id INT REFERENCES products(product_id),
                raw_feed_name VARCHAR(150),
                weekly_feed_kg NUMERIC(10, 2) NOT NULL,
                weekly_feed_cost NUMERIC(10, 2),
                ultimo_tope_kg NUMERIC(10, 2) NOT NULL,
                sampled_weight_g NUMERIC(6, 2) NOT NULL,
                weekly_growth_g NUMERIC(5, 2),
                weekly_fcr NUMERIC(4, 2),

                calculated_feed_rate_pct NUMERIC(5, 3),
                calculated_animals_by_tope NUMERIC(12, 2),
                calculated_survival_tope_pct NUMERIC(5, 2),

                calibrated_survival_pct NUMERIC(5, 2) NOT NULL,
                projected_density_per_ha NUMERIC(10, 2),
                manual_projected_growth_g NUMERIC(5, 2) NOT NULL,
                target_weight_g NUMERIC(6, 2) NOT NULL,
                projected_feed_rate_pct NUMERIC(5, 3),
                nuevo_tope_kg NUMERIC(10, 2) NOT NULL,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_cycle_log_date UNIQUE (cycle_id, log_date)
            );
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("Database schema successfully generated with weekly_pond_logs!")

    except Exception as e:
        print(f"Error during database setup: {e}")

if __name__ == "__main__":
    create_tables()
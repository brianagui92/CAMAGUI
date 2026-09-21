import os
import argparse
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")


def create_core_tables(cur):
    print("Rebuilding core enterprise tables...")
    cur.execute("""
                DROP TABLE IF EXISTS ponds CASCADE;
                DROP TABLE IF EXISTS farms CASCADE;
                DROP TABLE IF EXISTS organizations CASCADE;

                CREATE TABLE organizations
                (
                    org_id     SERIAL PRIMARY KEY,
                    name       VARCHAR(150) NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE farms
                (
                    farm_id    SERIAL PRIMARY KEY,
                    org_id     INT          NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
                    name       VARCHAR(150) NOT NULL UNIQUE,
                    location   VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE ponds
                (
                    pond_id    SERIAL PRIMARY KEY,
                    farm_id    INT           NOT NULL REFERENCES farms (farm_id) ON DELETE CASCADE,
                    pond_name  VARCHAR(50)   NOT NULL,
                    hectares   NUMERIC(6, 2) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)


def create_product_tables(cur):
    print("Rebuilding products, prices, and curves...")
    cur.execute("""
                DROP TABLE IF EXISTS growth_curves CASCADE;
                DROP TABLE IF EXISTS feeding_curves CASCADE;
                DROP TABLE IF EXISTS product_prices CASCADE;
                DROP TABLE IF EXISTS products CASCADE;

                CREATE TABLE products
                (
                    product_id        SERIAL PRIMARY KEY,
                    name              VARCHAR(150)       NOT NULL,
                    product_code      VARCHAR(50) UNIQUE NOT NULL, -- The new bridge column
                    category          VARCHAR(50)        NOT NULL,
                    base_unit         VARCHAR(20) DEFAULT 'kg',
                    package_weight_kg NUMERIC(6, 2)
                );

                CREATE TABLE product_prices
                (
                    price_id       SERIAL PRIMARY KEY,
                    product_id     INT            NOT NULL REFERENCES products (product_id) ON DELETE CASCADE,
                    package_price  NUMERIC(10, 2) NOT NULL, -- Total cost of the bag/package
                    effective_date DATE           NOT NULL,
                    end_date       DATE,
                    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Dynamic View to calculate price/kg on the fly
                CREATE OR REPLACE VIEW vw_current_product_prices AS
                SELECT pp.price_id,
                       p.product_id,
                       p.name                                                        AS product_name,
                       p.package_weight_kg,
                       pp.package_price,
                       ROUND((pp.package_price / NULLIF(p.package_weight_kg, 0)), 4) AS price_per_kg,
                       pp.effective_date,
                       pp.end_date
                FROM product_prices pp
                         JOIN products p ON pp.product_id = p.product_id;

                CREATE TABLE feeding_curves
                (
                    curve_id          SERIAL PRIMARY KEY,
                    curve_name        VARCHAR(50)   NOT NULL,
                    weight_waypoint_g NUMERIC(6, 2) NOT NULL,
                    target_bw_pct     NUMERIC(6, 3) NOT NULL, -- The smoothed, single percentage
                    CONSTRAINT unique_curve_waypoint UNIQUE (curve_name, weight_waypoint_g),
                    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE growth_curves
                (
                    growth_curve_id        SERIAL PRIMARY KEY,
                    name                   VARCHAR(100)  NOT NULL,
                    min_weight_g           NUMERIC(5, 2) NOT NULL,
                    max_weight_g           NUMERIC(5, 2) NOT NULL,
                    expected_weekly_gain_g NUMERIC(5, 2) NOT NULL,
                    notes                  TEXT,
                    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)


def create_operation_tables(cur):
    print("Rebuilding nursery (precría) and growout tracking tables...")
    cur.execute("""
                DROP TABLE IF EXISTS precria_transfers CASCADE;
                DROP TABLE IF EXISTS precria_feed_applications CASCADE;
                DROP TABLE IF EXISTS precria_batches CASCADE;
                DROP TABLE IF EXISTS larvae_providers CASCADE;
                DROP TABLE IF EXISTS growout_cycles CASCADE;

                CREATE TABLE growout_cycles
                (
                    cycle_id         SERIAL PRIMARY KEY,
                    cycle_code       VARCHAR(50) NOT NULL UNIQUE,
                    farm_id          INT         NOT NULL REFERENCES farms (farm_id) ON DELETE CASCADE,
                    pond_id          INT         NOT NULL REFERENCES ponds (pond_id) ON DELETE CASCADE,
                    stocking_date    DATE        NOT NULL,
                    initial_weight_g NUMERIC(5, 2),
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE larvae_providers
                (
                    provider_id  SERIAL PRIMARY KEY,
                    name         VARCHAR(150) NOT NULL UNIQUE,
                    contact_info VARCHAR(200),
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE precria_batches
                (
                    precria_batch_id      SERIAL PRIMARY KEY,
                    farm_id               INT                NOT NULL REFERENCES farms (farm_id) ON DELETE CASCADE,
                    batch_code            VARCHAR(50) UNIQUE NOT NULL,
                    start_date            DATE               NOT NULL,
                    initial_animals       INT                NOT NULL,

                    -- New operational costs
                    larvae_cost           NUMERIC(10, 2) DEFAULT 0.0,
                    ground_transport_cost NUMERIC(10, 2) DEFAULT 0.0,
                    sea_transport_cost    NUMERIC(10, 2) DEFAULT 0.0,

                    created_at            TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE precria_feed_applications
                (
                    feed_app_id      SERIAL PRIMARY KEY,
                    precria_batch_id INT NOT NULL REFERENCES precria_batches (precria_batch_id) ON DELETE CASCADE,
                    raw_product_name VARCHAR(150),
                    quantity_kg      NUMERIC(10, 2),
                    cost             NUMERIC(10, 2),
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE precria_transfers
                (
                    transfer_id         SERIAL PRIMARY KEY,
                    precria_batch_id    INT  NOT NULL REFERENCES precria_batches (precria_batch_id) ON DELETE CASCADE,
                    cycle_id            INT  NOT NULL REFERENCES growout_cycles (cycle_id) ON DELETE CASCADE,

                    -- Physical facts only (no prorated calculations)
                    transfer_date       DATE NOT NULL,
                    animals_transferred INT  NOT NULL,
                    transfer_weight_g   NUMERIC(10, 3),

                    CONSTRAINT unique_batch_cycle UNIQUE (precria_batch_id, cycle_id),
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)


def create_log_tables(cur):
    print("Rebuilding weekly logs table...")
    cur.execute("""
                DROP TABLE IF EXISTS weekly_pond_logs CASCADE;

                CREATE TABLE weekly_pond_logs
                (
                    log_id           SERIAL PRIMARY KEY,
                    cycle_id         INT  NOT NULL REFERENCES growout_cycles (cycle_id) ON DELETE CASCADE,
                    log_date         DATE NOT NULL,

                    -- The Core Realities (Inputs)
                    ultimo_tope_kg   NUMERIC(10, 2),
                    feed_consumed_kg NUMERIC(10, 2),
                    product_id       INT REFERENCES products (product_id), -- <-- The Relational Link
                    actual_weight_g  NUMERIC(10, 2),

                    CONSTRAINT unique_cycle_date UNIQUE (cycle_id, log_date),
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)


def create_harvest_tables(cur):
    print("Rebuilding harvest logs table...")
    cur.execute("""
                DROP TABLE IF EXISTS harvests CASCADE;

                DO
                $$
                    BEGIN
                        CREATE TYPE harvest_type_enum AS ENUM ('raleo', 'final', 'repano');
                    EXCEPTION
                        WHEN duplicate_object THEN null;
                    END
                $$;

                CREATE TABLE harvests
                (
                    harvest_id       SERIAL PRIMARY KEY,
                    cycle_id         INT                NOT NULL REFERENCES growout_cycles (cycle_id) ON DELETE CASCADE,
                    harvest_code     VARCHAR(50) UNIQUE NOT NULL,
                    harvest_date     DATE               NOT NULL,
                    harvest_type     harvest_type_enum  NOT NULL DEFAULT 'final',

                    -- Weight Accounting
                    lbs_remitidas    NUMERIC(10, 2)     NOT NULL, -- Weighed at farm harvest
                    lbs_planta       NUMERIC(10, 2)     NOT NULL, -- Liquidated by processing plant
                    average_weight_g NUMERIC(6, 2)      NOT NULL, -- Gramaje promedio

                    -- Financial Settlement
                    payment_received NUMERIC(12, 2)     NOT NULL,
                    packing_plant    VARCHAR(100),

                    created_at       TIMESTAMP                   DEFAULT CURRENT_TIMESTAMP
                );
                """)


def main():
    parser = argparse.ArgumentParser(description="CAMAGUI Database Setup CLI Tool")
    parser.add_argument("--core", action="store_true", help="Rebuild core tables (organizations, farms, ponds)")
    parser.add_argument("--products", action="store_true", help="Rebuild products, prices, and curves tables")
    parser.add_argument("--operations", action="store_true", help="Rebuild nursery and growout tracking tables")
    parser.add_argument("--logs", action="store_true", help="Rebuild weekly logs table")
    parser.add_argument("--harvests", action="store_true", help="Rebuild harvest logs table")
    parser.add_argument("--all", action="store_true", help="Rebuild all database tables")

    args = parser.parse_args()

    # Automatically show help if no arguments provided
    if not any([args.core, args.products, args.operations, args.logs, args.harvests, args.all]):
        parser.print_help()
        return

    try:
        print("Connecting to Supabase for database setup...")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        if args.core or args.all:
            create_core_tables(cur)

        if args.products or args.all:
            create_product_tables(cur)

        if args.operations or args.all:
            create_operation_tables(cur)

        if args.logs or args.all:
            create_log_tables(cur)

        if args.harvests or args.all:
            create_harvest_tables(cur)

        conn.commit()
        cur.close()
        conn.close()
        print("\nDatabase schema setup complete!")

    except Exception as e:
        print(f"Error during database setup: {e}")


if __name__ == "__main__":
    main()
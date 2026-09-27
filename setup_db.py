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

                    -- Financial Costs Restored
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


def create_analytical_views(cur):
    print("Rebuilding analytical views...")
    
    # ==========================================
    # 1. PRECRIA PERFORMANCE & FINANCIAL VIEW
    # ==========================================
    cur.execute("""
                CREATE OR REPLACE VIEW vw_precria_performance AS
                WITH feed_totals AS (
                    SELECT 
                        precria_batch_id,
                        SUM(quantity_kg) AS total_feed_kg,
                        SUM(cost) AS total_feed_cost
                    FROM precria_feed_applications
                    GROUP BY precria_batch_id
                ),
                transfer_totals AS (
                    SELECT 
                        precria_batch_id,
                        SUM(animals_transferred) AS total_animals_transferred,
                        MAX(transfer_date) AS last_transfer_date,
                        -- We calculate the weighted average transfer weight (peso de siembra)
                        SUM(animals_transferred * transfer_weight_g) / NULLIF(SUM(animals_transferred), 0) AS avg_transfer_weight_g,
                        -- Total biomass out in kg
                        SUM(animals_transferred * transfer_weight_g) / 1000 AS biomass_transferred_kg
                    FROM precria_transfers
                    GROUP BY precria_batch_id
                )
                SELECT 
                    pb.precria_batch_id,
                    pb.batch_code,
                    f.name AS farm_name,
                    pb.start_date,
                    pb.initial_animals AS stocked_larvae,
                    
                    tt.total_animals_transferred AS juveniles_transferred,
                    (tt.last_transfer_date - pb.start_date) AS days_in_nursery,
                    
                    -- Biological Performance
                    (tt.total_animals_transferred::numeric / NULLIF(pb.initial_animals, 0)) * 100 AS survival_rate_pct,
                    tt.avg_transfer_weight_g,
                    COALESCE(ft.total_feed_kg, 0) AS total_feed_kg,
                    -- FCR (Feed Conversion Ratio) for the nursery phase
                    COALESCE(ft.total_feed_kg, 0) / NULLIF(tt.biomass_transferred_kg, 0) AS fcr,
                    
                    -- Financials
                    pb.larvae_cost,
                    (pb.ground_transport_cost + pb.sea_transport_cost) AS total_transport_cost,
                    COALESCE(ft.total_feed_cost, 0) AS total_feed_cost,
                    
                    (pb.larvae_cost + pb.ground_transport_cost + pb.sea_transport_cost + COALESCE(ft.total_feed_cost, 0)) AS total_batch_cost,
                    
                    -- The ultimate industry metric: Unit Cost to transfer to Growout
                    (pb.larvae_cost + pb.ground_transport_cost + pb.sea_transport_cost + COALESCE(ft.total_feed_cost, 0)) / 
                        NULLIF(tt.total_animals_transferred / 1000.0, 0) AS cost_per_thousand_juveniles
                        
                FROM precria_batches pb
                JOIN farms f ON pb.farm_id = f.farm_id
                LEFT JOIN feed_totals ft ON pb.precria_batch_id = ft.precria_batch_id
                LEFT JOIN transfer_totals tt ON pb.precria_batch_id = tt.precria_batch_id;
                """)

    # ==========================================
    # 2. GROWOUT BIOLOGICAL PERFORMANCE VIEW
    # ==========================================
    cur.execute("""
                CREATE OR REPLACE VIEW vw_cycle_performance_bio AS
                WITH transfer_totals AS (
                    SELECT cycle_id, SUM(animals_transferred) as total_animals_transferred
                    FROM precria_transfers
                    GROUP BY cycle_id
                ),
                
                -- CTE 1: Calculate the absolute total feed consumed by each entire batch
                batch_feed AS (
                    SELECT precria_batch_id, SUM(quantity_kg) as total_feed_kg
                    FROM precria_feed_applications
                    GROUP BY precria_batch_id
                ),
                
                -- CTE 2: Calculate the absolute total animals that SURVIVED and were transferred out of the batch
                batch_transfer_totals AS (
                    SELECT precria_batch_id, SUM(animals_transferred) as total_outbound_animals
                    FROM precria_transfers
                    GROUP BY precria_batch_id
                ),
                
                -- CTE 3: Safely assign the prorated feed to the growout cycle
                precria_feed_totals AS (
                    SELECT 
                        pt.cycle_id,
                        -- Prorate by dividing this cycle's animals by the total animals successfully transferred out of the batch
                        SUM(bf.total_feed_kg * (pt.animals_transferred::numeric / NULLIF(btt.total_outbound_animals, 0))) AS precria_feed_kg
                    FROM precria_transfers pt
                    JOIN batch_feed bf ON pt.precria_batch_id = bf.precria_batch_id
                    JOIN batch_transfer_totals btt ON pt.precria_batch_id = btt.precria_batch_id
                    GROUP BY pt.cycle_id
                ),
                
                growout_feed_totals AS (
                    SELECT 
                        cycle_id, 
                        SUM(feed_consumed_kg) AS growout_feed_kg,
                        MODE() WITHIN GROUP (ORDER BY product_id) AS top_feed_product_id
                    FROM weekly_pond_logs
                    GROUP BY cycle_id
                ),
                
                harvest_totals AS (
                    SELECT 
                        cycle_id,
                        SUM(lbs_remitidas) AS total_lbs_remitidas,
                        MAX(harvest_date) AS last_harvest_date,
                        SUM((lbs_remitidas * 453.592) / NULLIF(average_weight_g, 0)) AS estimated_animals_harvested,
                        SUM(lbs_remitidas * average_weight_g) / NULLIF(SUM(lbs_remitidas), 0) AS weighted_avg_harvest_weight_g
                    FROM harvests
                    GROUP BY cycle_id
                ),
                
                final_harvest_data AS (
                    SELECT DISTINCT ON (cycle_id) 
                        cycle_id, 
                        average_weight_g AS final_harvest_weight_g, 
                        harvest_date AS date_of_final
                    FROM harvests
                    WHERE harvest_type = 'final'
                    ORDER BY cycle_id, harvest_date DESC
                )
                
                SELECT 
                    gc.cycle_id,
                    gc.cycle_code,
                    p.pond_name,
                    gc.stocking_date,
                    tt.total_animals_transferred,
                    (tt.total_animals_transferred / p.hectares) AS stocking_density_ha,
                    COALESCE(pf.precria_feed_kg, 0) + COALESCE(gf.growout_feed_kg, 0) AS total_feed_consumed_kg,
                    prod.name AS most_used_feed_type,
                    
                    ht.total_lbs_remitidas,
                    (ht.total_lbs_remitidas / p.hectares) AS lbs_harvested_per_ha,
                    
                    ht.weighted_avg_harvest_weight_g,
                    fhd.final_harvest_weight_g,
                    fhd.date_of_final,
                    ht.last_harvest_date,
                    
                    (ht.last_harvest_date - gc.stocking_date) AS total_days_of_cycle,
                    
                    -- Survival Rate %
                    (ht.estimated_animals_harvested / NULLIF(tt.total_animals_transferred, 0)) * 100 AS survival_rate_pct,
                    
                    -- FCR = Feed (kg) / Biomass Harvested (kg)
                    (COALESCE(pf.precria_feed_kg, 0) + COALESCE(gf.growout_feed_kg, 0)) / 
                        NULLIF(ht.total_lbs_remitidas * 0.453592, 0) AS fcr,
                        
                    -- Avg Weekly Growth (g/week) calculated up to 'final' harvest
                    (fhd.final_harvest_weight_g - gc.initial_weight_g) / 
                        NULLIF((fhd.date_of_final - gc.stocking_date) / 7.0, 0) AS avg_weekly_growth_g
                        
                FROM growout_cycles gc
                JOIN ponds p ON gc.pond_id = p.pond_id
                LEFT JOIN transfer_totals tt ON gc.cycle_id = tt.cycle_id
                LEFT JOIN precria_feed_totals pf ON gc.cycle_id = pf.cycle_id
                LEFT JOIN growout_feed_totals gf ON gc.cycle_id = gf.cycle_id
                LEFT JOIN products prod ON gf.top_feed_product_id = prod.product_id
                LEFT JOIN harvest_totals ht ON gc.cycle_id = ht.cycle_id
                LEFT JOIN final_harvest_data fhd ON gc.cycle_id = fhd.cycle_id;
                """)


def main():
    parser = argparse.ArgumentParser(description="CAMAGUI Database Setup CLI Tool")
    parser.add_argument("--core", action="store_true", help="Rebuild core tables (organizations, farms, ponds)")
    parser.add_argument("--products", action="store_true", help="Rebuild products, prices, and curves tables")
    parser.add_argument("--operations", action="store_true", help="Rebuild nursery and growout tracking tables")
    parser.add_argument("--logs", action="store_true", help="Rebuild weekly logs table")
    parser.add_argument("--harvests", action="store_true", help="Rebuild harvest logs table")
    parser.add_argument("--analytics", action="store_true", help="Rebuild analytical SQL Views")
    parser.add_argument("--all", action="store_true", help="Rebuild all database tables and views")

    args = parser.parse_args()

    # Automatically show help if no arguments provided
    if not any([args.core, args.products, args.operations, args.logs, args.harvests, args.analytics, args.all]):
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

        if args.analytics or args.all:
            create_analytical_views(cur)

        conn.commit()
        cur.close()
        conn.close()
        print("\nDatabase schema setup complete!")

    except Exception as e:
        print(f"Error during database setup: {e}")


if __name__ == "__main__":
    main()
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# We execute an SQL database structural upgrade script to physically embed your Python AI mathematics inside PostgreSQL
def run_migration():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    # 1. Create a table strictly to store the parameters the AI Engine computes.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS growth_models (
        model_name VARCHAR(100) UNIQUE PRIMARY KEY,
        w_inf NUMERIC(10,5),
        k NUMERIC(10,5),
        t0 NUMERIC(10,5),
        b NUMERIC(10,5),
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Add an elegant SQL Function that automatically inverses and computes the geometry
    cur.execute("""
    CREATE OR REPLACE FUNCTION predict_weight_vbgf(
        p_model_name VARCHAR,
        p_current_weight NUMERIC,
        p_days_ahead NUMERIC
    ) RETURNS NUMERIC AS $$
    DECLARE
        v_winf NUMERIC;
        v_k NUMERIC;
        v_t0 NUMERIC;
        v_b NUMERIC;
        v_doc NUMERIC;
        v_future_doc NUMERIC;
        v_future_weight NUMERIC;
        v_ratio NUMERIC;
        v_bracket NUMERIC;
    BEGIN
        -- Find the parameters from the AI python app
        SELECT w_inf, k, t0, b INTO v_winf, v_k, v_t0, v_b 
        FROM growth_models WHERE model_name = p_model_name;
        
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;

        IF p_current_weight >= v_winf - 0.2 THEN
            RETURN ROUND(v_winf, 2);
        END IF;
        
        IF p_current_weight < 0 THEN
            p_current_weight := 0;
        END IF;

        v_ratio := p_current_weight / v_winf;
        IF v_ratio > 0.999 THEN v_ratio := 0.999; END IF;

        -- Step 1: Inverse Biology (Calculate precise physical age based purely on current weight)
        v_doc := v_t0 - (1.0 / v_k) * LN(1.0 - POWER(v_ratio, 1.0 / v_b));
        
        -- Step 2: Forward Projection (Advance timeline to future state)
        v_future_doc := v_doc + p_days_ahead;
        
        v_bracket := 1.0 - EXP(-v_k * (v_future_doc - v_t0));
        IF v_bracket < 0 THEN v_bracket := 0; END IF;
        
        -- Step 3: Compute final mass
        v_future_weight := v_winf * POWER(v_bracket, v_b);

        RETURN ROUND(v_future_weight, 2);
    END;
    $$ LANGUAGE plpgsql;
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Database Migration completed beautifully.")

if __name__ == "__main__":
    run_migration()

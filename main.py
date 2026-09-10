import os
import psycopg2
from dotenv import load_dotenv

# Load the hidden connection string from the .env file
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

def setup_database():
    try:
        print("Connecting to Supabase...")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        print("Creating the 'farm_workers' table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS farm_workers (
                worker_id SERIAL PRIMARY KEY,
                farm_id INT NOT NULL,
                name VARCHAR(100) NOT NULL,
                role VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        print("Inserting a test record...")
        cur.execute("""
            INSERT INTO farm_workers (farm_id, name, role)
            VALUES (%s, %s, %s)
            RETURNING worker_id, name;
        """, (1, 'Carlos', 'Pond Technician'))

        new_worker = cur.fetchone()
        print(f"Success! Inserted {new_worker[1]} with ID: {new_worker[0]}")

        conn.commit()
        cur.close()
        conn.close()
        print("Database connection closed.")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    setup_database()
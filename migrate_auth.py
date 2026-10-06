import os
import psycopg2
import hashlib
from dotenv import load_dotenv

load_dotenv()

def hash_password(password):
    # Using SHA-256 for simple zero-dependency robust internal hashing
    return hashlib.sha256(password.encode()).hexdigest()

def setup_auth_database():
    print("Connecting to database to build authentication layer...")
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    
    # Create users table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS farm_users (
        username VARCHAR(50) PRIMARY KEY,
        password_hash VARCHAR(100) NOT NULL,
        role VARCHAR(20) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # Master Admin Account (Eduardo)
    admin_user = 'eduardo'
    admin_pass = 'camagui_master'
    
    # Assistant Data-Entry Account
    assistant_user = 'asistente'
    assistant_pass = 'finca2026'
    
    # Read-Only Viewer Account (Optional, for investors/partners)
    viewer_user = 'visita'
    viewer_pass = 'ver2026'
    
    users = [
        (admin_user, hash_password(admin_pass), 'admin'),
        (assistant_user, hash_password(assistant_pass), 'assistant'),
        (viewer_user, hash_password(viewer_pass), 'viewer')
    ]
    
    for u, p, r in users:
        cur.execute("""
            INSERT INTO farm_users (username, password_hash, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE 
            SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role;
        """, (u, p, r))

    conn.commit()
    cur.close()
    conn.close()
    print("Authentication system successfully deployed!")
    print("--- ACCOUNTS CREATED ---")
    print(f"Admin: {admin_user} / {admin_pass}")
    print(f"Assistant: {assistant_user} / {assistant_pass}")
    print(f"Viewer: {viewer_user} / {viewer_pass}")

if __name__ == "__main__":
    setup_auth_database()
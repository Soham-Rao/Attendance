import sqlite3
import sys
import os

# Add parent directory to path to import utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.hashing import calculate_hash

def migrate_hashes():
    print("Starting hash migration...")
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Fetch all records ordered by ID
    c.execute("SELECT id, student_id, subject, date, status, hour FROM attendance ORDER BY id ASC")
    records = c.fetchall()
    
    previous_hash = "0" * 64  # Genesis hash
    
    for record in records:
        record_id, student_id, subject, date, status, hour = record
        
        # Calculate new hash
        current_hash = calculate_hash(student_id, subject, date, status, hour, previous_hash)
        
        # Update record
        c.execute("UPDATE attendance SET previous_hash=?, current_hash=? WHERE id=?",
                  (previous_hash, current_hash, record_id))
        
        previous_hash = current_hash
        
    conn.commit()
    conn.close()
    print(f"Successfully migrated {len(records)} records.")

if __name__ == "__main__":
    migrate_hashes()

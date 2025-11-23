import sqlite3
import sys
import os
import json

# Add parent directory to path if running from root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.hashing import calculate_hash

def verify_chain():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    c.execute("SELECT id, student_id, subject, date, status, hour, previous_hash, current_hash FROM attendance ORDER BY id ASC")
    records = c.fetchall()
    
    conn.close()
    
    if not records:
        return {"status": "valid", "message": "No records found. Chain is empty but valid."}
        
    expected_previous_hash = "0" * 64  # Genesis hash
    
    for i, record in enumerate(records):
        record_id, student_id, subject, date, status, hour, stored_prev_hash, stored_curr_hash = record
        
        # 1. Check if stored previous_hash matches what we expect from the previous record
        if stored_prev_hash != expected_previous_hash:
            return {
                "status": "tampered",
                "message": f"Chain broken at Record ID {record_id}. Previous hash mismatch.",
                "details": {
                    "record_id": record_id,
                    "expected_previous_hash": expected_previous_hash,
                    "stored_previous_hash": stored_prev_hash
                }
            }
            
        # 2. Recalculate hash for this record
        calculated_hash = calculate_hash(student_id, subject, date, status, hour, stored_prev_hash)
        
        # 3. Check if calculated hash matches stored current_hash
        if calculated_hash != stored_curr_hash:
            return {
                "status": "tampered",
                "message": f"Data tampering detected at Record ID {record_id}. Hash mismatch.",
                "details": {
                    "record_id": record_id,
                    "calculated_hash": calculated_hash,
                    "stored_hash": stored_curr_hash
                }
            }
            
        # Update expected hash for next iteration
        expected_previous_hash = calculated_hash
        
    return {"status": "valid", "message": f"Integrity verified. All {len(records)} records are valid."}

if __name__ == "__main__":
    result = verify_chain()
    print(json.dumps(result, indent=4))

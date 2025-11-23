import hashlib
import json

def calculate_hash(student_id, subject, date, status, hour, previous_hash):
    """
    Calculates the SHA-256 hash for an attendance record.
    The hash is based on the record's data and the previous record's hash.
    """
    # Create a dictionary of the record data
    record_data = {
        "student_id": student_id,
        "subject": subject,
        "date": date,
        "status": status,
        "hour": hour,
        "previous_hash": previous_hash
    }
    
    # Sort keys to ensure consistent ordering for hashing
    record_string = json.dumps(record_data, sort_keys=True)
    
    # Generate SHA-256 hash
    return hashlib.sha256(record_string.encode()).hexdigest()

def get_last_hash(cursor):
    """
    Retrieves the current_hash of the most recently inserted attendance record.
    Returns '0' (genesis hash) if no records exist.
    """
    cursor.execute("SELECT current_hash FROM attendance ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row and row[0]:
        return row[0]
    return "0" * 64  # Genesis hash (64 zeros)

def recalculate_chain(cursor, start_id):
    """
    Recalculates the hash chain starting from a specific record ID.
    This is used when a record is modified (approved change).
    """
    # 1. Get the previous hash for the start_id record
    # We need the current_hash of the record BEFORE start_id
    cursor.execute("SELECT current_hash FROM attendance WHERE id < ? ORDER BY id DESC LIMIT 1", (start_id,))
    row = cursor.fetchone()
    previous_hash = row[0] if row else "0" * 64
    
    # 2. Fetch all records starting from start_id
    cursor.execute("SELECT id, student_id, subject, date, status, hour FROM attendance WHERE id >= ? ORDER BY id ASC", (start_id,))
    records = cursor.fetchall()
    
    # 3. Iterate and update
    for record in records:
        rec_id, student_id, subject, date, status, hour = record
        
        # Calculate new hash
        new_hash = calculate_hash(student_id, subject, date, status, hour, previous_hash)
        
        # Update DB
        cursor.execute("UPDATE attendance SET previous_hash=?, current_hash=? WHERE id=?", 
                       (previous_hash, new_hash, rec_id))
        
        # Set previous_hash for next iteration
        previous_hash = new_hash

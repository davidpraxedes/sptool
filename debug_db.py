import psycopg2
import json

DATABASE_URL = "postgresql://postgres:ZciydaCzmAgnGnzrztdzmMONpqHEPNxK@yamabiko.proxy.rlwy.net:32069/railway"

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT id, reference_data_json, waymb_data_json FROM orders ORDER BY id DESC LIMIT 5")
    rows = cur.fetchall()
    
    print(f"Found {len(rows)} orders.\n")
    
    for row in rows:
        print(f"--- Order ID: {row[0]} ---")
        try:
            ref_data = json.loads(row[1]) if row[1] else {}
            print("Reference Data (Bumps might be here):")
            print(json.dumps(ref_data, indent=2))
        except:
            print(f"Ref Data (Raw): {row[1]}")

        print("\n")
        
    cur.close()
    conn.close()

except Exception as e:
    print(f"Error: {e}")

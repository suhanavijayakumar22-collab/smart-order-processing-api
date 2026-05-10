import mysql.connector
from datetime import datetime

def seed_database():
    try:
        # Connect to MySQL
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="password", 
            database="onlineshopping2"
        )
        cursor = conn.cursor()

        # 1. Add a Storage Area
        cursor.execute("""
            INSERT IGNORE INTO Storage_Areas (storage_id, max_capacity, current_load, working_status)
            VALUES ('AREA-A', 500, 0, 'active')
        """)

        # 2. Add a Customer (Customer ID will auto-increment to 1)
        cursor.execute("""
            INSERT IGNORE INTO Customers (location, reward_points)
            VALUES ('New York, NY', 100)
        """)

        # 3. Add some Products to the pantry
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        products_data = [
            ('P-101', 'Wireless Mouse', 'Electronics', 25.99, 'AREA-A', 50, current_time),
            ('P-102', 'Mechanical Keyboard', 'Electronics', 89.99, 'AREA-A', 10, current_time),
            ('P-103', 'Coffee Mug', 'Home', 12.50, 'AREA-A', 5, current_time)
        ]
        
        cursor.executemany("""
            INSERT IGNORE INTO Products (product_id, name, category, price, storage_id, stock_count, stored_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, products_data)

        # Save it all
        conn.commit()
        print("Success! Dummy data has been added to the database.")

    except mysql.connector.Error as err:
        print(f"Error: {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    seed_database()
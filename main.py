from fastapi import FastAPI, HTTPException
import mysql.connector

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import mysql.connector

from datetime import datetime, timedelta
import uuid

from datetime import datetime
import uuid

# --- PYDANTIC MODELS (The Bouncer) ---

# This represents one item in the shopping cart
class OrderItem(BaseModel):
    product_id: str
    quantity: int

# This represents the full order ticket the customer submits
class CreateOrderRequest(BaseModel):
    customer_id: int
    delivery_type: str # The PDF constraint: Must be 'normal' or 'fast'
    items: List[OrderItem]

class ReturnRequest(BaseModel):
    order_id: str
    product_id: str
    reason: str

class ReturnInspection(BaseModel):
    action: str  # Must be 'approve' or 'reject'
    is_damaged: bool


# 1. Start the FastAPI app
app = FastAPI(title="Smart Order Processing API")

# 2. Set up the Database Connection
# Replace "YOUR_PASSWORD" with the password you use for MySQL root!
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="password", 
            database="onlineshopping2"
        )
        return conn
    except mysql.connector.Error as err:
        print(f"Error connecting to MySQL: {err}")
        return None

# 3. Create your first Route (The Waiter)
@app.get("/")
def read_root():
    return {"message": "Welcome to the Smart Online Shopping API! The kitchen is open."}

# 4. Create a test route to check the database connection
@app.get("/db-status")
def check_db():
    conn = get_db_connection()
    if conn and conn.is_connected():
        conn.close()
        return {"status": "Success! Connected to MySQL."}
    else:
        raise HTTPException(status_code=500, detail="Database connection failed.")

@app.post("/place-order")
def place_order(order: CreateOrderRequest):
    # 1. Bouncer Check (From your code!)
    if order.delivery_type not in ["normal", "fast"]:
        raise HTTPException(status_code=400, detail="Delivery type must be 'normal' or 'fast'")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")

    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # 2. Check Product Availability Constraint
        for item in order.items:
            cursor.execute("SELECT stock_count, name FROM Products WHERE product_id = %s", (item.product_id,))
            product = cursor.fetchone()

            if not product:
                raise ValueError(f"Product {item.product_id} not found.")
            if product["stock_count"] < item.quantity:
                raise ValueError(f"Not enough stock for {product['name']}. Only {product['stock_count']} left!")

        # 3. ASSIGN A STORAGE AREA (Single-Location Preference)
        product_ids = [item.product_id for item in order.items]
        format_strings = ','.join(['%s'] * len(product_ids))
        cursor.execute(f"SELECT DISTINCT storage_id FROM Products WHERE product_id IN ({format_strings})", product_ids)
        
        preferred_areas = [row["storage_id"] for row in cursor.fetchall()]
        assigned_storage_id = None

        for area_id in preferred_areas:
            cursor.execute("SELECT storage_id FROM Storage_Areas WHERE storage_id = %s AND current_load < max_capacity AND working_status = 'active'", (area_id,))
            area = cursor.fetchone()
            if area:
                assigned_storage_id = area["storage_id"]
                break 

        if not assigned_storage_id:
            cursor.execute("SELECT storage_id FROM Storage_Areas WHERE current_load < max_capacity AND working_status = 'active' LIMIT 1")
            backup_area = cursor.fetchone()
            if not backup_area:
                raise ValueError("System Overloaded: No storage areas have capacity right now.")
            assigned_storage_id = backup_area["storage_id"]

        # 4. CREATE THE ORDER (*** THIS IS THE STEP YOUR CODE WAS MISSING! ***)
        new_order_id = "ORD-" + str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Orders (order_id, customer_id, delivery_type, status, assigned_storage_id, order_time) 
            VALUES (%s, %s, %s, 'placed', %s, %s)
        """, (new_order_id, order.customer_id, order.delivery_type, assigned_storage_id, datetime.now()))

        # 5. DEDUCT THE STOCK (FIFO) AND SAVE ITEMS
        for item in order.items:
            # Save to bridge table
            cursor.execute(
                "INSERT INTO Order_Items (order_id, product_id, quantity) VALUES (%s, %s, %s)",
                (new_order_id, item.product_id, item.quantity)
            )
            # Deduct stock
            cursor.execute("""
                UPDATE Products 
                SET stock_count = stock_count - %s 
                WHERE product_id = %s 
                ORDER BY stored_date ASC 
                LIMIT 1
            """, (item.quantity, item.product_id))

        # 6. SYSTEM CAPACITY & REWARD POINTS
        cursor.execute("UPDATE Storage_Areas SET current_load = current_load + 1 WHERE storage_id = %s", (assigned_storage_id,))
        cursor.execute("UPDATE Customers SET reward_points = reward_points + 10 WHERE customer_id = %s", (order.customer_id,))

        conn.commit()
        return {
            "message": "Success! Order placed and stock deducted.",
            "order_id": new_order_id
        }

    except ValueError as ve:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(ve))
    
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Transaction failed: {str(e)}")

    finally:
        cursor.close()
        conn.close()


@app.post("/pack-order/{counter_id}")
def pack_next_order(counter_id: str):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")

    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # 1. Check if this counter is working
        # 1. FAILURE HANDLING & CAPACITY CONSTRAINTS
        # Fetch the counter's status AND its maximum capacity
        cursor.execute("SELECT working_status, max_orders_per_hour FROM Packing_Counters WHERE counter_id = %s", (counter_id,))
        counter = cursor.fetchone()
        
        if not counter:
            raise ValueError(f"Counter {counter_id} does not exist.")
        if counter["working_status"] != 'active':
            raise ValueError(f"Counter {counter_id} is currently unavailable/failed.")

        # Check how many orders are currently sitting at this counter waiting for dispatch
        cursor.execute("SELECT COUNT(*) as current_load FROM Orders WHERE assigned_counter_id = %s AND status = 'packed'", (counter_id,))
        current_load = cursor.fetchone()["current_load"]

        if current_load >= counter["max_orders_per_hour"]:
            raise ValueError(f"Capacity Reached! Counter {counter_id} is full ({current_load}/{counter['max_orders_per_hour']}). Dispatch some orders first!")

        # 2. Find the most urgent order (Fast delivery goes first!)
        cursor.execute("""
            SELECT order_id, assigned_storage_id FROM Orders 
            WHERE status = 'placed' 
            ORDER BY delivery_type = 'fast' DESC, order_time ASC 
            LIMIT 1
        """)
        next_order = cursor.fetchone()

        if not next_order:
            return {"message": "No pending orders to pack! Take a break."}

        order_id_to_pack = next_order["order_id"]
        storage_id = next_order["assigned_storage_id"]

        # 3. Mark it as packed
        cursor.execute("""
            UPDATE Orders 
            SET status = 'packed', assigned_counter_id = %s 
            WHERE order_id = %s
        """, (counter_id, order_id_to_pack))

        # 4. Free up storage space
        if storage_id:
            cursor.execute("""
                UPDATE Storage_Areas 
                SET current_load = current_load - 1 
                WHERE storage_id = %s
            """, (storage_id,))

        conn.commit()

        return {
            "message": "Order successfully packed!",
            "packed_order_id": order_id_to_pack,
            "packed_at_counter": counter_id
        }

    except ValueError as ve:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(ve))
    
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Transaction failed: {str(e)}")

    finally:
        cursor.close()
        conn.close()



@app.post("/dispatch-batch")
def dispatch_orders(courier_capacity: int = 5):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")

    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # 1. BATCH PROCESSING CONSTRAINT: Grab a batch of packed orders
        # We prioritize 'fast' delivery first, up to the courier's max capacity
        cursor.execute("""
            SELECT order_id FROM Orders 
            WHERE status = 'packed' 
            ORDER BY delivery_type = 'fast' DESC, order_time ASC 
            LIMIT %s
        """, (courier_capacity,))
        
        packed_orders = cursor.fetchall()

        if not packed_orders:
            return {"message": "No packed orders waiting for dispatch! The courier leaves empty-handed."}

        # Extract just the order IDs into a Python list
        order_ids_to_dispatch = [order["order_id"] for order in packed_orders]
        current_time = datetime.now()

        # 2. DISPATCH SLOT CONSTRAINT: Update all selected orders at once
        # This dynamically creates a query like: WHERE order_id IN ('ORD-123', 'ORD-456')
        format_strings = ','.join(['%s'] * len(order_ids_to_dispatch))
        update_query = f"""
            UPDATE Orders 
            SET status = 'dispatched', dispatch_slot = %s 
            WHERE order_id IN ({format_strings})
        """
        
        # We combine the current time and the list of IDs for the query parameters
        query_params = [current_time] + order_ids_to_dispatch
        cursor.execute(update_query, query_params)

        # 3. COMMIT THE TRANSACTION
        conn.commit()

        return {
            "message": f"Success! Dispatched {len(order_ids_to_dispatch)} orders.",
            "dispatched_order_ids": order_ids_to_dispatch,
            "courier_pickup_time": current_time
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Transaction failed: {str(e)}")

    finally:
        cursor.close()
        conn.close()


@app.post("/request-return")
def request_return(request: ReturnRequest):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")

    cursor = conn.cursor(dictionary=True)

    try:
        # 1. Fetch the original order
        cursor.execute("SELECT dispatch_slot, status FROM Orders WHERE order_id = %s", (request.order_id,))
        order = cursor.fetchone()

        if not order:
            raise ValueError("Order not found.")
        if order["status"] != 'dispatched':
            raise ValueError("Order has not been dispatched yet. Cannot return.")

        # 2. RETURN TIME WINDOW CONSTRAINT: Check the 7-day rule
        dispatch_date = order["dispatch_slot"]
        if not dispatch_date:
            raise ValueError("Dispatch date is missing.")
            
        # FIX: Just in case MySQL hands us a string instead of a real datetime object!
        if isinstance(dispatch_date, str):
            dispatch_date = datetime.strptime(dispatch_date, '%Y-%m-%d %H:%M:%S')

        days_since_dispatch = (datetime.now() - dispatch_date).days
        
        if days_since_dispatch > 7:
            raise ValueError(f"Return rejected. It has been {days_since_dispatch} days. Returns are only allowed within 7 days.")

        # 3. Create a pending return ticket
        # 3. Create a pending return ticket
        cursor.execute("""
            INSERT INTO Returns (order_id, product_id, reason, request_date, inspection_status)
            VALUES (%s, %s, %s, %s, 'pending')
        """, (request.order_id, request.product_id, request.reason, datetime.now())) # <-- request.reason added here!

        conn.commit()
        return {"message": "Return request submitted successfully. Awaiting warehouse inspection."}

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    
    # NEW FIX: This safety net catches raw crashes and puts them in the browser!
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Server crashed! Here is why: {str(e)}")
        
    finally:
        cursor.close()
        conn.close()


@app.post("/inspect-return/{return_id}")
def inspect_return(return_id: int, inspection: ReturnInspection):
    # Enforce basic validation
    if inspection.action not in ['approve', 'reject']:
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")

    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # 1. Get the return ticket details
        cursor.execute("SELECT * FROM Returns WHERE return_id = %s", (return_id,))
        ret_ticket = cursor.fetchone()

        if not ret_ticket or ret_ticket["inspection_status"] != 'pending':
            raise ValueError("Return ticket not found or already processed.")

        # 2. If REJECTED
        if inspection.action == 'reject':
            cursor.execute("UPDATE Returns SET inspection_status = 'rejected' WHERE return_id = %s", (return_id,))
            conn.commit()
            return {"message": "Return rejected. No refund issued."}

        # 3. If APPROVED
        # Fetch product price and customer ID to process refunds and points
        cursor.execute("SELECT price FROM Products WHERE product_id = %s", (ret_ticket["product_id"],))
        product = cursor.fetchone()
        
        cursor.execute("SELECT customer_id FROM Orders WHERE order_id = %s", (ret_ticket["order_id"],))
        customer = cursor.fetchone()

        # 4. RETURN HANDLING CONSTRAINT: Damaged goods check
        restocking_status = 'discarded' if inspection.is_damaged else 'restocked'
        
        if not inspection.is_damaged:
            # Put it back on the shelf!
            cursor.execute(
                "UPDATE Products SET stock_count = stock_count + 1 WHERE product_id = %s",
                (ret_ticket["product_id"],)
            )

        # 5. REWARD POINTS CONSTRAINT: Deduct points (Let's assume 10 points deducted per return)
        cursor.execute(
            "UPDATE Customers SET reward_points = GREATEST(0, reward_points - 10) WHERE customer_id = %s",
            (customer["customer_id"],)
        )

        # 6. Finalize the Return Ticket
        cursor.execute("""
            UPDATE Returns 
            SET inspection_status = 'approved', restocking_status = %s, refund_amount = %s 
            WHERE return_id = %s
        """, (restocking_status, product["price"], return_id))

        conn.commit()
        return {
            "message": "Return approved and processed successfully.",
            "refunded_amount": product["price"],
            "restocked": not inspection.is_damaged,
            "points_deducted": 10
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Transaction failed: {str(e)}")
    finally:
        cursor.close()
        conn.close()


@app.get("/system-status")
def get_system_status():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 1. Count how many orders are sitting in the warehouse
        cursor.execute("SELECT COUNT(*) as pending_orders FROM Orders WHERE status IN ('placed', 'packed')")
        pending_orders = cursor.fetchone()["pending_orders"]
        
        # 2. Check the health and capacity of Storage Areas
        cursor.execute("SELECT storage_id, current_load, max_capacity, working_status FROM Storage_Areas")
        storage_areas = cursor.fetchall()
        
        # 3. Check the health of the Packing Counters
        cursor.execute("SELECT counter_id, max_orders_per_hour, working_status FROM Packing_Counters")
        counters = cursor.fetchall()
        
        return {
            "warehouse_health": "Online 🟢",
            "active_orders_in_building": pending_orders,
            "storage_areas": storage_areas,
            "packing_counters": counters
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch status: {str(e)}")
    finally:
        cursor.close()
        conn.close()
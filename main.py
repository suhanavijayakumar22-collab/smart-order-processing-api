from fastapi import FastAPI, HTTPException
import mysql.connector

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import mysql.connector

from datetime import datetime, timedelta
import uuid

from datetime import datetime
import uuid
import math

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


# Add this CORS block directly below it:
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allows any webpage to talk to your API
    allow_credentials=True,
    allow_methods=["*"], # Allows POST, GET, etc.
    allow_headers=["*"],
)

# 2. Set up the Database Connection
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

# --- LOGISTICS ROUTING MATRIX (Estimated Hours) ---
# Format: { "Warehouse Location": { "Customer Location": Hours } }
DELIVERY_MATRIX = {
    "New York, NY":    {"New York, NY": 2,  "Miami, FL": 30, "Chicago, IL": 14, "Los Angeles, CA": 42},
    "Chicago, IL":     {"New York, NY": 14, "Miami, FL": 22, "Chicago, IL": 2,  "Los Angeles, CA": 30},
    "Los Angeles, CA": {"New York, NY": 42, "Miami, FL": 40, "Chicago, IL": 30, "Los Angeles, CA": 2},
    "Miami, FL":       {"New York, NY": 30, "Miami, FL": 2,  "Chicago, IL": 22, "Los Angeles, CA": 40}
}

@app.post("/place-order")
def place_order(order: CreateOrderRequest):
    # 1. Bouncer Check
    if order.delivery_type not in ["normal", "fast"]:
        raise HTTPException(status_code=400, detail="Delivery type must be 'normal' or 'fast'")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")

    # Using the buffered cursor to prevent unread result errors
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    try:
        conn.start_transaction()
        
        # 2. Fetch Customer Geography Details
        cursor.execute("SELECT location, x_coord, y_coord FROM Customers WHERE customer_id = %s", (order.customer_id,))
        customer = cursor.fetchone()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found.")
        cust_location = customer["location"]
        cust_x = customer["x_coord"]
        cust_y = customer["y_coord"]

        # For this prototype, we evaluate routing based on the primary product in the order
        first_item = order.items[0]

        # 3. DYNAMIC INVENTORY + FIFO + GEOGRAPHIC ROUTING ENGINE
        # This single query filters out warehouses that are full, inactive, or completely out of stock,
        # while sorting batches chronologically by oldest date first (FIFO compliance).
        cursor.execute("""
            SELECT p.batch_id, p.storage_id, p.stock_count, p.stored_date,
                   sa.location AS warehouse_location, sa.x_coord AS wh_x, sa.y_coord AS wh_y
            FROM Products p
            JOIN Storage_Areas sa ON p.storage_id = sa.storage_id
            WHERE p.product_id = %s 
              AND p.stock_count >= %s
              AND sa.working_status = 'active'
              AND sa.current_load < sa.max_capacity
            ORDER BY p.stored_date ASC
        """, (first_item.product_id, first_item.quantity))
        
        candidates = cursor.fetchall()

        # 4. WAITLIST CONSTRAINT TRIGGER (If no valid warehouse has stock/capacity)
        if not candidates:
            cursor.execute("SELECT SUM(stock_count) as total FROM Products WHERE product_id = %s", (first_item.product_id,))
            stock_check = cursor.fetchone()
            total_stock = stock_check["total"] if stock_check and stock_check["total"] else 0
            
            # If the entire company is out of stock, queue the order
            if total_stock < first_item.quantity:
                new_order_id = "ORD-" + str(uuid.uuid4())[:8]
                cursor.execute("""
                    INSERT INTO Orders (order_id, customer_id, delivery_type, status, assigned_storage_id, order_time) 
                    VALUES (%s, %s, %s, 'delayed', 'WAITLIST', %s)
                """, (new_order_id, order.customer_id, order.delivery_type, datetime.now()))
                
                for item in order.items:
                    cursor.execute(
                        "INSERT INTO Order_Items (order_id, product_id, quantity) VALUES (%s, %s, %s)",
                        (new_order_id, item.product_id, item.quantity)
                    )
                conn.commit()
                return {
                    "message": f"Order delayed. Not enough stock for Product {first_item.product_id}. Processing on next restock sweep.",
                    "order_id": new_order_id,
                    "status": "delayed"
                }
            else:
                raise ValueError("Logistics Overload: Active warehouse nodes holding this item are at maximum capacity.")

        # 5. SPATIAL ROUTING (Evaluate the filtered candidates)
        best_candidate = None
        shortest_time = float('inf')
        winning_location = ""

        for cand in candidates:
            wh_loc = cand["warehouse_location"]
            # Attempt 1: Matrix Match
            est_hours = DELIVERY_MATRIX.get(wh_loc, {}).get(cust_location)
            
            # Attempt 2: Coordinate Fallback
            if est_hours is None:
                dist = math.sqrt((cand["wh_x"] - cust_x)**2 + (cand["wh_y"] - cust_y)**2)
                est_hours = round(dist * 0.5, 1)

            # Proximity Check
            if est_hours < shortest_time:
                shortest_time = est_hours
                best_candidate = cand
                winning_location = wh_loc

        assigned_storage_id = best_candidate["storage_id"]
        winning_batch_id = best_candidate["batch_id"]
        new_order_id = "ORD-" + str(uuid.uuid4())[:8]

        # 6. CHECK STAFF PACKING CAPACITY CONSTRAINT
        cursor.execute("""
            SELECT counter_id, staff_available, max_orders_per_hour, current_load 
            FROM Packing_Counters 
            WHERE current_load < (staff_available * max_orders_per_hour) 
            AND working_status = 'active'
            ORDER BY current_load ASC 
            LIMIT 1
        """)
        counter = cursor.fetchone()

        if not counter:
            raise ValueError("Order delayed. All packing staff are currently at maximum capacity.")
            
        assigned_counter = counter["counter_id"]

       # 7. COMMIT EXECUTION CHANGES
        
        # A. Deduct inventory from the specific winning batch (FIFO Optimized)
        cursor.execute("""
            UPDATE Products SET stock_count = stock_count - %s WHERE batch_id = %s
        """, (first_item.quantity, winning_batch_id))

        # B. Insert final master order record FIRST (Creates the parent 'box')
        cursor.execute("""
            INSERT INTO Orders (order_id, customer_id, delivery_type, status, assigned_storage_id, order_time) 
            VALUES (%s, %s, %s, 'placed', %s, %s)
        """, (new_order_id, order.customer_id, order.delivery_type, assigned_storage_id, datetime.now()))

        # C. Save items to bridge table SECOND (Puts items in the 'box')
        for item in order.items:
            cursor.execute(
                "INSERT INTO Order_Items (order_id, product_id, quantity) VALUES (%s, %s, %s)",
                (new_order_id, item.product_id, item.quantity)
            )

        # D. Update Warehouse and Counter Load Metrics
        cursor.execute("UPDATE Storage_Areas SET current_load = current_load + 1 WHERE storage_id = %s", (assigned_storage_id,))
        cursor.execute("UPDATE Packing_Counters SET current_load = current_load + 1 WHERE counter_id = %s", (assigned_counter,))
        
        # E. Add Customer Reward Points
        cursor.execute("UPDATE Customers SET reward_points = reward_points + 10 WHERE customer_id = %s", (order.customer_id,))

        conn.commit()

        return {
            "message": "Success! Order placed.",
            "order_id": new_order_id,
            "assigned_warehouse": assigned_storage_id,
            "warehouse_location": winning_location,
            "assigned_counter": assigned_counter,
            "estimated_delivery_time": f"{shortest_time} hours"
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

    cursor = conn.cursor(dictionary=True, buffered=True)

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

    cursor = conn.cursor(dictionary=True, buffered=True)

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

    cursor = conn.cursor(dictionary=True, buffered=True)

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
        new_return_id = cursor.lastrowid

        conn.commit()
        return {"message": "Return request submitted successfully. Awaiting warehouse inspection.",
        "return_id": new_return_id}

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

    cursor = conn.cursor(dictionary=True, buffered=True)

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
    
    cursor = conn.cursor(dictionary=True, buffered=True)
    
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

@app.post("/process-delayed-orders")
def process_delayed_orders():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database is down!")
    
    cursor = conn.cursor(dictionary=True, buffered=True)
    processed_count = 0

    try:
        # OPEN THE DOOR ONCE AT THE VERY BEGINNING
        conn.start_transaction() 

        # 1. Find all delayed orders, sorting by oldest first
        cursor.execute("SELECT * FROM Orders WHERE status = 'delayed' ORDER BY order_time ASC")
        delayed_orders = cursor.fetchall()

        for order in delayed_orders:
            order_id = order['order_id']
            
            # Fetch the items the customer was waiting for
            cursor.execute("SELECT product_id, quantity FROM Order_Items WHERE order_id = %s", (order_id,))
            items = cursor.fetchall()
            
            # 2. Check if we finally have enough stock
            can_fulfill = True
            for item in items:
                cursor.execute("SELECT stock_count FROM Products WHERE product_id = %s", (item['product_id'],))
                prod = cursor.fetchone()
                if not prod or prod['stock_count'] < item['quantity']:
                    can_fulfill = False
                    break 
            
            # 3. If we have the stock, fulfill it!
            if can_fulfill:
                # Deduct stock (FIFO)
                for item in items:
                    cursor.execute("""
                        UPDATE Products SET stock_count = stock_count - %s 
                        WHERE product_id = %s ORDER BY stored_date ASC LIMIT 1
                    """, (item['quantity'], item['product_id']))
                
                # Find an empty storage area
                cursor.execute("SELECT storage_id FROM Storage_Areas WHERE current_load < max_capacity AND working_status = 'active' LIMIT 1")
                area = cursor.fetchone()
                if not area:
                    raise ValueError("No storage space available to process delayed orders.")
                
                # Take up warehouse space
                cursor.execute("UPDATE Storage_Areas SET current_load = current_load + 1 WHERE storage_id = %s", (area['storage_id'],))
                
                # Change status from 'delayed' to 'placed' and assign storage
                cursor.execute("UPDATE Orders SET status = 'placed', assigned_storage_id = %s WHERE order_id = %s", (area['storage_id'], order_id))
                
                # Give the customer their reward points!
                cursor.execute("UPDATE Customers SET reward_points = reward_points + 10 WHERE customer_id = %s", (order['customer_id'],))
                
                processed_count += 1

        # SAVE EVERYTHING AT ONCE AT THE VERY END
        conn.commit()
        
        return {"message": f"Swept the waitlist! Successfully fulfilled {processed_count} delayed orders."}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/batch-packing-schedule")
def get_batch_schedule():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    # Using our trusty buffered cursor!
    cursor = conn.cursor(dictionary=True, buffered=True)

    try:
        # 1. Fetch all pending items that haven't been packed yet
        # We join Orders, Order_Items, and Products to get the delivery urgency and the category.
        cursor.execute("""
            SELECT 
                o.order_id, 
                o.delivery_type, 
                p.category, 
                p.name AS product_name, 
                oi.quantity
            FROM Orders o
            JOIN Order_Items oi ON o.order_id = oi.order_id
            JOIN Products p ON oi.product_id = p.product_id
            WHERE o.status = 'placed'
        """)
        
        pending_items = cursor.fetchall()
        
        # 2. The Batching Algorithm
        batches = {}
        total_items = 0
        
        for item in pending_items:
            # Create the batch key (e.g., "fast-Electronics" or "normal-Furniture")
            batch_key = f"{item['delivery_type']}-{item['category']}"
            
            if batch_key not in batches:
                batches[batch_key] = []
                
            # Append the item to its specific batch manifest
            batches[batch_key].append({
                "order_id": item['order_id'],
                "product": item['product_name'],
                "quantity": item['quantity']
            })
            total_items += item['quantity']

        return {
            "message": "Batch schedule generated successfully.",
            "total_pending_items": total_items,
            "packing_batches": batches
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate batches: {str(e)}")
    finally:
        cursor.close()
        conn.close()
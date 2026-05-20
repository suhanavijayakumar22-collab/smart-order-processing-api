import streamlit as st
import requests

# The URL where your FastAPI server is listening
API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="Warehouse Command Center", page_icon="📦")
st.title("📦 Warehouse Command Center")

# Create tabs for different operations
tab1, tab2, tab3 = st.tabs(["🛒 Place Order", "⏳ Process Waitlist", "📦 View Batches"])

# --- TAB 1: PLACE ORDER ---
with tab1:
    st.subheader("Simulate a Customer Order")
    
    col1, col2 = st.columns(2)
    with col1:
        customer_map = {
            1: "New York, NY",
            2: "Los Angeles, CA",
            3: "Chicago, IL",
            4: "Miami, FL"
        }
        
        # This creates a dropdown that shows "1 - New York, NY"
        customer_id = st.selectbox(
            "Select Customer", 
            options=list(customer_map.keys()), 
            format_func=lambda x: f"ID: {x} - {customer_map[x]}"
        )
        
        delivery_type = st.selectbox("Delivery Type", ["normal", "fast"])
    with col2:
        product_id = st.text_input("Product ID", value="P-201")
        quantity = st.number_input("Quantity", min_value=1, value=1)

    if st.button("Submit Order", type="primary"):
        # When the button is clicked, we build the JSON and send it to FastAPI
        payload = {
            "customer_id": int(customer_id),
            "delivery_type": delivery_type,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": int(quantity)
                }
            ]
        }
        
        try:
            response = requests.post(f"{API_URL}/place-order", json=payload)
            data = response.json()
            
            # Show a green success box or a red error box based on the API status
            if response.status_code == 200:
                st.success("API Call Successful!")
                st.json(data) # This prints your cool spatial routing JSON!
            else:
                st.error("API Error!")
                st.json(data)
        except requests.exceptions.ConnectionError:
            st.error("Cannot connect to API. Is your FastAPI server running on port 8000?")

# --- TAB 2: PROCESS DELAYED ORDERS ---
with tab2:
    st.subheader("Sweep the Waitlist")
    st.write("Click below to trigger the warehouse to process any delayed backorders.")
    
    if st.button("Process Delayed Orders"):
        try:
            response = requests.post(f"{API_URL}/process-delayed-orders")
            if response.status_code == 200:
                st.success(response.json()["message"])
            else:
                st.error(response.json())
        except:
            st.error("Connection Error.")

# --- TAB 3: BATCH PROCESSING SCHEDULE ---
with tab3:
    st.subheader("Generate Worker Manifest")
    st.write("Group all active orders by delivery type and category.")
    
    if st.button("Generate Batches"):
        try:
            response = requests.get(f"{API_URL}/batch-packing-schedule")
            if response.status_code == 200:
                st.success("Batches Generated Successfully!")
                st.json(response.json())
            else:
                st.error(response.json())
        except:
            st.error("Connection Error.")
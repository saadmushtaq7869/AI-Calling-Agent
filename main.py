import os
import resend
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment Variables
MONGODB_URL = os.getenv("MONGODB_URL")
DATABASE_NAME = os.getenv("DATABASE_NAME", "vapi_database")
SALES_TEAM_EMAIL = os.getenv("SALES_TEAM_EMAIL")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

# Initialize Resend API Key
resend.api_key = RESEND_API_KEY

# Database Setup
try:
    client = MongoClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    leads_collection = db["leads"]
    print("Connected to MongoDB successfully.")
except Exception as e:
    print(f"MongoDB Connection Warning: {e}")

def send_email_notification(subject: str, body_text: str, recipient_email: str):
    """Sends emails via Resend HTTP API (Port 443) to bypass Render SMTP blocks."""
    if not RESEND_API_KEY:
        print("[Email Error] RESEND_API_KEY is not set in environment variables.")
        return False

    try:
        params = {
            "from": "onboarding@resend.dev",  # Resend default testing domain
            "to": [recipient_email],
            "subject": subject,
            "text": body_text,
        }
        response = resend.Emails.send(params)
        print(f"[Email Success] Email sent successfully via HTTP API! ID: {response}")
        return True
    except Exception as e:
        print(f"[Email Failure] Failed to send email via Resend: {e}")
        return False

@app.get("/")
def read_root():
    return {"status": "Vapi Backend API is running!"}

@app.post("/vapi/webhook")
async def vapi_webhook(request: Request):
    payload = await request.json()
    message = payload.get("message", {})
    message_type = message.get("type")

    if message_type == "tool-calls":
        tool_calls = message.get("toolCalls", [])
        if not tool_calls:
            return {"status": "no tool calls found"}

        tool_call = tool_calls[0]
        tool_id = tool_call.get("id")
        function_data = tool_call.get("function", {})
        function_name = function_data.get("name")
        args = function_data.get("arguments", {})

        print(f"[Tool Triggered] Function: {function_name} | Args: {args}")

        if function_name == "create_purchase_lead":
            name = args.get("name", "Unknown")
            phone = args.get("phone", "Not provided")
            product = args.get("product", "Unspecified Product")
            email = args.get("email", "Not provided")
            address = args.get("address", "Not provided")
            notes = args.get("notes", "")

            # 1. Save to MongoDB
            try:
                leads_collection.insert_one({
                    "name": name,
                    "phone": phone,
                    "product": product,
                    "email": email,
                    "address": address,
                    "notes": notes,
                    "type": "purchase_lead"
                })
                print("[Database] Lead saved to MongoDB.")
            except Exception as mongo_err:
                print(f"[Database Error] Could not save to MongoDB: {mongo_err}")

            # 2. Email Sales Team
            sales_email_body = (
                f"New Purchase Lead Captured:\n\n"
                f"Name: {name}\nPhone: {phone}\nProduct: {product}\n"
                f"Email: {email}\nAddress: {address}\nNotes: {notes}"
            )
            if SALES_TEAM_EMAIL:
                send_email_notification("New Purchase Lead Received", sales_email_body, SALES_TEAM_EMAIL)

            # 3. Email Buyer
            if "@" in str(email):
                clean_email = email.strip()
                buyer_email_body = (
                    f"Hi {name},\n\n"
                    f"Thank you for your order! We have received your request for: {product}.\n"
                    f"Our team will contact you shortly at {phone}."
                )
                send_email_notification(f"Order Confirmation - {product}", buyer_email_body, clean_email)

            # 4. Immediate Return to Vapi
            return {
                "results": [{
                    "toolCallId": tool_id,
                    "result": f"Order for {product} recorded successfully."
                }]
            }

    return {"status": "event processed"}
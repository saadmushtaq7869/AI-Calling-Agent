import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

app = FastAPI()

# Enable CORS for Netlify frontend
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
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SALES_TEAM_EMAIL = os.getenv("SALES_TEAM_EMAIL")

# Database Setup
try:
    client = MongoClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    leads_collection = db["leads"]
    print("Connected to MongoDB successfully.")
except Exception as e:
    print(f"MongoDB Connection Warning: {e}")

def send_email_notification(subject: str, body_text: str, recipient_email: str):
    """Safely sends email notifications via Gmail SMTP using SSL (Port 465)."""
    if not SMTP_USER or not SMTP_PASS:
        print("[SMTP Error] Missing SMTP_USER or SMTP_PASS environment variables.")
        return False

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))

    try:
        # Use SMTP_SSL specifically to bypass Render's Port 587 block
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        server.login(SMTP_USER, SMTP_PASS.replace(" ", ""))
        server.send_message(msg)
        server.quit()
        print(f"[SMTP Success] Email successfully sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"[SMTP Failure] Failed to send email: {e}")
        return False

@app.get("/")
def read_root():
    return {"status": "Vapi Backend API is running!"}

@app.post("/vapi/webhook")
async def vapi_webhook(request: Request):
    payload = await request.json()
    message = payload.get("message", {})
    message_type = message.get("type")

    # Respond to Vapi tool calls
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

        # Handle Lead Creation
        if function_name == "create_purchase_lead":
            name = args.get("name", "Unknown")
            phone = args.get("phone", "Not provided")
            product = args.get("product", "Unspecified Product")
            email = args.get("email", "Not provided")
            address = args.get("address", "Not provided")
            notes = args.get("notes", "")

            # Combine extra info if passed into notes
            full_details = f"Address: {address} | Notes: {notes}"

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
                f"Name: {name}\n"
                f"Phone: {phone}\n"
                f"Product: {product}\n"
                f"Email: {email}\n"
                f"Address: {address}\n"
                f"Notes: {notes}"
            )
            send_email_notification("New Purchase Lead Received", sales_email_body, SALES_TEAM_EMAIL)

            # 3. Email Buyer (if valid email provided)
            if "@" in str(email):
                clean_email = email.strip()
                buyer_email_body = (
                    f"Hi {name},\n\n"
                    f"Thank you for your order! We have received your request for: {product}.\n"
                    f"Our team will contact you shortly at {phone}."
                )
                send_email_notification(f"Order Confirmation - {product}", buyer_email_body, clean_email)

            # 4. Return success payload to Vapi AI
            return {
                "results": [{
                    "toolCallId": tool_id,
                    "result": f"Order for {product} recorded successfully. Confirmation email sent."
                }]
            }

    return {"status": "event processed"}
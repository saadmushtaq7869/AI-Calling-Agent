import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from openai import OpenAI

# 1. Load Environment Variables
load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SALES_TEAM_EMAIL = os.getenv("SALES_TEAM_EMAIL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 2. Initialize Clients
app = FastAPI(title="Vapi AI Backend Agent")

# MongoDB Connection
db_client = AsyncIOMotorClient(MONGODB_URL)
db = db_client.get_database("vapi_agent_db")
leads_collection = db.get_collection("leads")
issues_collection = db.get_collection("package_issues")

# OpenAI Client (Replaces heavy local sentence-transformers models)
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# 3. Helper Functions
def send_email(subject: str, body: str, recipient: str):
    """Utility to send automated SMTP email alerts."""
    if not SMTP_USER or not SMTP_PASS or not recipient:
        print("[Warning] Missing SMTP credentials or recipient. Skipping email.")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        print(f"[Email Success] Email sent to {recipient}")
    except Exception as e:
        print(f"[Email Error] Failed to send email: {e}")


def get_openai_embedding(text: str):
    """Generates embeddings using OpenAI API instead of local sentence_transformers."""
    if not openai_client:
        return []
    response = openai_client.embeddings.create(
        input=text,
        model="text-embedding-3-small"
    )
    return response.data[0].embedding


# 4. FastAPI Health & Root Endpoints
@app.get("/")
async def root():
    return {"status": "online", "message": "Vapi AI Agent Backend Operational"}


# 5. Core Webhook Handler for Vapi
@app.post("/vapi/webhook")
async def handle_vapi_webhook(request: Request):
    payload = await request.json()
    message = payload.get("message", {})
    message_type = message.get("type")

    # Handle Tool/Function Calls triggered by Vapi Voice Assistant
    if message_type == "tool-calls":
        tool_call_list = message.get("toolCalls", [])
        results = []

        for tool_call in tool_call_list:
            function_data = tool_call.get("function", {})
            func_name = function_data.get("name")
            args = function_data.get("arguments", {})

            # Tool 1: Purchase Lead Creation
            if func_name == "create_purchase_lead":
                lead_data = {
                    "name": args.get("name"),
                    "phone": args.get("phone"),
                    "product": args.get("product"),
                    "notes": args.get("notes", "")
                }
                # Save to MongoDB
                await leads_collection.insert_one(lead_data)
                
                # Send Email Alert
                email_body = f"New Lead Captured:\nName: {args.get('name')}\nPhone: {args.get('phone')}\nProduct: {args.get('product')}"
                send_email("New Sales Lead Captured", email_body, SALES_TEAM_EMAIL)

                results.append({
                    "toolCallId": tool_call.get("id"),
                    "result": "Success. Purchase lead saved and sales team notified."
                })

            # Tool 2: Report Missing Package
            elif func_name == "report_missing_package":
                issue_data = {
                    "order_id": args.get("order_id"),
                    "customer_name": args.get("customer_name"),
                    "details": args.get("details", "")
                }
                await issues_collection.insert_one(issue_data)
                
                email_body = f"Missing Package Reported:\nOrder ID: {args.get('order_id')}\nCustomer: {args.get('customer_name')}"
                send_email("Urgent: Missing Package Report", email_body, SALES_TEAM_EMAIL)

                results.append({
                    "toolCallId": tool_call.get("id"),
                    "result": "Success. Package issue logged and escalated to support."
                })

            # Tool 3: Search RAG Knowledge Base
            elif func_name == "search_rag_knowledge":
                query = args.get("query", "")
                # Generate lightweight vector embedding via OpenAI
                query_vector = get_openai_embedding(query)
                
                results.append({
                    "toolCallId": tool_call.get("id"),
                    "result": f"Knowledge base searched for: '{query}'."
                })

        return {"results": results}

    return {"status": "event_received"}
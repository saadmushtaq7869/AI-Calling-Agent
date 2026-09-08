import os
import uuid
import smtplib
import numpy as np
from typing import Dict, Any, List
from email.message import EmailMessage
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

# Environment Credentials
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "ai_calling_agent")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SALES_TEAM_EMAIL = os.getenv("SALES_TEAM_EMAIL")

app = FastAPI(title="AI Calling Agent Backend (MongoDB + RAG)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize MongoDB Async Client & OpenAI
mongo_client = AsyncIOMotorClient(MONGODB_URL)
db = mongo_client[DB_NAME]
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# =============================================================
# IN-MEMORY VECTOR STORE & RAG ENGINE
# =============================================================
print("Loading Embedding Model for RAG...")
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

class VectorStore:
    def __init__(self):
        self.documents: List[str] = []
        self.embeddings: List[np.ndarray] = []
        # Pre-seed with default delivery handling knowledge
        self.add_knowledge("Default gate rule: Leave packages near the main front gate unless instructed otherwise.")
        self.add_knowledge("Side gate rule: Place packages behind the fence at the side gate if requested.")
        self.add_knowledge("Back gate rule: Keep packages tucked behind the planters at the back gate out of sight.")

    def add_knowledge(self, text: str):
        if text in self.documents:
            return
        vector = embedding_model.encode(text)
        self.documents.append(text)
        self.embeddings.append(vector)
        print(f"[RAG Knowledge Base Updated]: Added -> {text}")

    def query(self, query_text: str, top_k: int = 2) -> List[str]:
        if not self.embeddings:
            return []
        query_vec = embedding_model.encode(query_text)
        
        # Calculate Cosine Similarities
        similarities = [
            np.dot(query_vec, doc_vec) / (np.linalg.norm(query_vec) * np.linalg.norm(doc_vec))
            for doc_vec in self.embeddings
        ]
        
        # Sort and take top matches
        top_indices = np.argsort(similarities)[::-1][:top_k]
        return [self.documents[i] for i in top_indices if similarities[i] > 0.3]

rag_store = VectorStore()


# =============================================================
# HELPER FUNCTIONS
# =============================================================
def send_email(to_email: str, subject: str, body: str):
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"Email sent successfully to {to_email}")
    except Exception as e:
        print(f"Failed to send email: {str(e)}")


# =============================================================
# LIVE CALL FUNCTIONS (Tools invoked by Vapi during the call)
# =============================================================

async def handle_create_purchase(args: Dict[str, Any]) -> str:
    name = args.get("name")
    phone = args.get("phone_number")
    address = args.get("address")
    product = args.get("product_name")
    email = args.get("email")
    delivery_notes = args.get("delivery_notes", "Standard delivery")

    delivery_id = f"DEL-{uuid.uuid4().hex[:6].upper()}"

    # 1. Save Order Document to MongoDB
    order_doc = {
        "delivery_id": delivery_id,
        "name": name,
        "phone": phone,
        "address": address,
        "product": product,
        "email": email or "N/A",
        "delivery_notes": delivery_notes,
        "status": "Order Placed"
    }
    await db["orders"].insert_one(order_doc)

    # 2. Email Confirmation to Client
    if email:
        email_body = f"""Hi {name},

Your order for '{product}' is confirmed!
Delivery ID: {delivery_id}
Address: {address}
Delivery Instructions: {delivery_notes}

Best regards,
Sales Team
"""
        send_email(email, f"Order Confirmation - Delivery ID: {delivery_id}", email_body)

    return f"Order created successfully in MongoDB. The delivery ID is {delivery_id}."


async def handle_missing_package(args: Dict[str, Any]) -> str:
    delivery_id = args.get("delivery_id")
    name = args.get("name")
    phone = args.get("phone_number")

    # 1. Update Order in MongoDB
    result = await db["orders"].update_one(
        {"delivery_id": delivery_id},
        {"$set": {"status": "Package Missing"}}
    )

    if result.matched_count == 0:
        # Save unverified inquiry if ID not found in database
        await db["orders"].insert_one({
            "delivery_id": delivery_id,
            "name": name,
            "phone": phone,
            "status": "Package Missing (Unverified)"
        })

    # 2. Alert Sales Team via Email
    sales_email_body = f"""ALERT: Missing Package Reported

Customer Name: {name}
Phone Number: {phone}
Delivery ID: {delivery_id}
Status: Escalated to Sales Support
"""
    send_email(SALES_TEAM_EMAIL, f"URGENT: Missing Package - {delivery_id}", sales_email_body)
    return "Status updated to Package Missing in database and escalated to sales team."


def handle_search_rag_knowledge(args: Dict[str, Any]) -> str:
    """Tool that allows AI to query vector knowledge base live during call."""
    query = args.get("query", "")
    results = rag_store.query(query)
    if results:
        return "Relevant Knowledge Base Instructions:\n" + "\n".join(f"- {r}" for r in results)
    return "No specific gate/delivery instructions found. Default to standard front gate procedure."


# =============================================================
# SELF-TRAINING & REFLECTION PIPELINE
# =============================================================
async def run_post_call_self_training(transcript: str, caller_phone: str):
    """Reflects on finished transcripts to extract new handling guidelines."""
    reflection_prompt = f"""
    Analyze the following customer call transcript.
    TRANSCRIPT:
    "{transcript}"

    TASK:
    1. Identify any specific customer preferences or instructions regarding delivery locations (e.g., side gate, back gate, leave with neighbor).
    2. Formulate a general rule/instruction for future calls based on this preference.
    3. Return ONLY the new rule as a single concise sentence. If no new rule is present, respond with 'NONE'.
    """

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": reflection_prompt}]
        )
        learned_rule = response.choices[0].message.content.strip()

        if learned_rule and learned_rule != "NONE":
            # Add new rule into the vector knowledge base automatically
            rag_store.add_knowledge(learned_rule)
            
            # Save learned rule in MongoDB rule log
            await db["learned_rules"].insert_one({
                "caller_phone": caller_phone,
                "rule": learned_rule
            })
            print(f"[Self-Training Complete]: MongoDB & Vector store updated with rule: {learned_rule}")

    except Exception as e:
        print(f"Error during self-training loop: {str(e)}")


# =============================================================
# MAIN VAPI WEBHOOK ENDPOINT
# =============================================================
@app.post("/vapi/webhook")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    message = payload.get("message", {})
    msg_type = message.get("type")

    # 1. Handle Real-Time Tool Execution Calls
    if msg_type == "tool-calls":
        results = []
        for tool_call in message.get("toolCallList", []):
            call_id = tool_call.get("id")
            func = tool_call.get("function", {})
            func_name = func.get("name")
            args = func.get("arguments", {})

            if func_name == "create_purchase_lead":
                res_text = await handle_create_purchase(args)
            elif func_name == "report_missing_package":
                res_text = await handle_missing_package(args)
            elif func_name == "search_rag_knowledge":
                res_text = handle_search_rag_knowledge(args)
            else:
                res_text = "Unknown tool call."

            results.append({
                "toolCallId": call_id,
                "result": res_text
            })

        return {"results": results}

    # 2. Trigger Post-Call Self-Training Loop
    elif msg_type == "end-of-call-report":
        transcript = message.get("transcript", "")
        caller_phone = message.get("customer", {}).get("number", "Unknown")
        background_tasks.add_task(run_post_call_self_training, transcript, caller_phone)
        return {"status": "self_training_scheduled"}

    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
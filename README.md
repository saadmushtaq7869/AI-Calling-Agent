# AI Voice Agent Sales Automation

An end-to-end AI-powered voice sales automation system. This application allows customers to interact with an AI calling agent via a web interface, collects order details during the call, persists lead information in MongoDB Atlas, and dispatches real-time email notifications to both the sales team and buyers via the Resend API.

---

## **Architecture Overview**

```text
[ Netlify Frontend ] ---> (Voice Stream) ---> [ Vapi AI Agent ]
                                                     |
                                            (HTTP Webhook Tool)
                                                     v
[ Buyer / Sales Email ] <--- [ Resend API ] <--- [ FastAPI (Render) ] ---> [ MongoDB Atlas ]

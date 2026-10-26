import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from notion_client import Client

load_dotenv()

# Inisialisasi Notion Client
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

notion = Client(auth=NOTION_API_KEY) if NOTION_API_KEY else None

app = FastAPI(
    title="Autonomous SEO Audit & Lead Enrichment API",
    description="AI-first backend combining FastAPI, LangChain, and LangSmith for automated SEO analysis.",
    version="1.1.0"
)

# 1. Schema Definitions
class AuditRequest(BaseModel):
    url: str = Field(..., example="https://example.com")
    target_keyword: str = Field(..., example="B2B SaaS Marketing")
    prospect_name: str = Field(default="Team", example="Alex")

class TallyField(BaseModel):
    key: Optional[str] = None
    label: str
    type: Optional[str] = None
    value: Any = None

class TallyData(BaseModel):
    fields: List[TallyField]

class TallyWebhookPayload(BaseModel):
    data: TallyData

class SEOAuditResult(BaseModel):
    seo_health_score: int = Field(description="Skor SEO halaman dari 1-100")
    missing_seo_elements: List[str] = Field(description="Daftar kelemahan teknis SEO")
    recommended_article_ideas: List[str] = Field(description="3 ide judul artikel SEO")
    cold_outreach_email: str = Field(description="Draft email B2B dingin")

# 2. Scraper On-Page
def scrape_onpage_seo(url: str) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SEOAuditBot/1.0)"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        title = soup.title.string.strip() if soup.title and soup.title.string else "MISSING"
        meta_desc_tag = soup.find("meta", attrs={"name": "description"})
        meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag and meta_desc_tag.get("content") else "MISSING"
        h1_tags = [h1.get_text(strip=True) for h1 in soup.find_all("h1")]
        h2_tags = [h2.get_text(strip=True) for h2 in soup.find_all("h2")][:10]
        paragraphs = " ".join([p.get_text(strip=True) for p in soup.find_all("p")])[:1500]

        return {
            "title": title,
            "meta_description": meta_desc,
            "h1_tags": h1_tags,
            "h2_tags": h2_tags,
            "content_sample": paragraphs
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal mengambil data URL: {str(e)}")

# 3. Penyimpan ke Notion
def save_to_notion(payload: AuditRequest, result: SEOAuditResult):
    if not notion or not NOTION_DATABASE_ID:
        return

    try:
        issues_text = "\n".join([f"• {item}" for item in result.missing_seo_elements])
        ideas_text = "\n".join([f"• {item}" for item in result.recommended_article_ideas])

        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Company URL": {"title": [{"text": {"content": payload.url}}]},
                "Company Name": {"rich_text": [{"text": {"content": payload.prospect_name or "N/A"}}]},
                "Target Keyword": {"rich_text": [{"text": {"content": payload.target_keyword}}]},
                "SEO Score": {"number": result.seo_health_score},
                "Technical Issues": {"rich_text": [{"text": {"content": issues_text[:2000]}}]},
                "Suggested Blog Titles": {"rich_text": [{"text": {"content": ideas_text[:2000]}}]},
                "Personalized Outreach Email": {"rich_text": [{"text": {"content": result.cold_outreach_email[:2000]}}]}
            }
        )
        print("[SUCCESS] Data audit berhasil disimpan ke Notion.")
    except Exception as e:
        print(f"[ERROR] Gagal menulis ke Notion: {str(e)}")

# 4. Notifikasi ke Telegram Bot
def send_telegram_alert(payload: AuditRequest, result: SEOAuditResult):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        message = (
            f"🎯 *New Lead Audited: {payload.prospect_name}*\n"
            f"🌐 *URL:* {payload.url}\n"
            f"🔑 *Target Keyword:* `{payload.target_keyword}`\n"
            f"📊 *SEO Score:* *{result.seo_health_score}/100*\n\n"
            f"✉️ *Cold Email Preview:*\n"
            f"_{result.cold_outreach_email[:250]}..._\n\n"
            f"📂 *Check Notion DB for full report.*"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=20)
        print("[SUCCESS] Notifikasi Telegram terkirim.")
    except Exception as e:
        print(f"[ERROR] Gagal mengirim alert Telegram: {str(e)}")

# 5. Core Execution Pipeline
def run_seo_pipeline(payload: AuditRequest) -> SEOAuditResult:
    scraped_data = scrape_onpage_seo(payload.url)

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
    structured_llm = llm.with_structured_output(SEOAuditResult)

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are an expert Technical SEO Architect and B2B Growth Strategist. "
            "Analyze the scraped on-page SEO metadata and content sample against the target keyword. "
            "Identify critical technical/content gaps, propose high-impact blog topics, and write a "
            "no-BS, hyper-personalized cold outreach email in English referencing their specific page gaps."
        )),
        ("human", (
            "Target URL: {url}\n"
            "Prospect Name: {prospect_name}\n"
            "Target Keyword: {target_keyword}\n\n"
            "--- SCRAPED ON-PAGE DATA ---\n"
            "Title Tag: {title}\n"
            "Meta Description: {meta_description}\n"
            "H1 Tags: {h1_tags}\n"
            "H2 Tags: {h2_tags}\n"
            "Body Content Sample: {content_sample}"
        ))
    ])

    chain = prompt | structured_llm
    result = chain.invoke({
        "url": payload.url,
        "prospect_name": payload.prospect_name,
        "target_keyword": payload.target_keyword,
        **scraped_data
    })

    # Simpan ke Notion & kirim notifikasi
    save_to_notion(payload, result)
    send_telegram_alert(payload, result)

    return result

# 6. Endpoints
@app.post("/api/v1/seo-audit", response_model=SEOAuditResult)
async def generate_seo_audit(payload: AuditRequest):
    """Endpoint sinkron untuk testing manual di Swagger UI."""
    return run_seo_pipeline(payload)

@app.post("/webhook/tally", status_code=202)
async def tally_webhook_handler(payload: TallyWebhookPayload, background_tasks: BackgroundTasks):
    """Endpoint asinkron non-blocking untuk menerima event webhook dari Tally."""
    fields_map = {f.label: f.value for f in payload.data.fields}

    target_url = fields_map.get("Target Website URL")
    target_keyword = fields_map.get("Primary Target Keyword")
    company_name = fields_map.get("Company / Brand Name") or "Team"

    if not target_url or not target_keyword:
        raise HTTPException(status_code=400, detail="Missing required fields from Tally.")

    audit_payload = AuditRequest(
        url=str(target_url).strip(),
        target_keyword=str(target_keyword).strip(),
        prospect_name=str(company_name).strip() if company_name else "Team"
    )

    # Eksekusi pipeline di background tanpa menahan webhook Tally
    background_tasks.add_task(run_seo_pipeline, audit_payload)

    return {
        "status": "accepted",
        "message": "Audit job queued successfully in background",
        "prospect": company_name
    }
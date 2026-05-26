import os, requests, asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime, timedelta

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")

# ── PubMed fetch ──────────────────────────────────────────────
def fetch_pubmed(keywords: str, days: int = 30, max_results: int = 5):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
    date_filter = f"{since}[dp]"
    query = keywords + " AND " + date_filter

    search = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={"db": "pubmed", "term": query, "retmax": max_results,
                "retmode": "json", "tool": "literiver", "email": "user@example.com"}
    ).json()

    pmids = search.get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []

    summary = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        params={"db": "pubmed", "id": ",".join(pmids),
                "retmode": "json", "tool": "literiver", "email": "user@example.com"}
    ).json()

    abstract_xml = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={"db": "pubmed", "id": ",".join(pmids),
                "retmode": "xml", "tool": "literiver", "email": "user@example.com"}
    ).text

    # Parse abstracts from XML
    import xml.etree.ElementTree as ET
    abstract_map = {}
    try:
        root = ET.fromstring(abstract_xml)
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID", "")
            texts = article.findall(".//AbstractText")
            abstract = " ".join(t.text or "" for t in texts if t.text)
            abstract_map[pmid] = abstract or "Abstract not available"
    except Exception:
        pass

    papers = []
    result = summary.get("result", {})
    for pmid in pmids:
        p = result.get(pmid, {})
        if not p:
            continue
        authors = p.get("authors", [])
        author_str = ", ".join(a["name"] for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        papers.append({
            "pmid": pmid,
            "title": p.get("title", "No title"),
            "authors": author_str or "Unknown",
            "journal": p.get("fulljournalname") or p.get("source", "PubMed"),
            "date": p.get("pubdate", ""),
            "abstract": abstract_map.get(pmid, "Abstract not available"),
        })
    return papers

# ── OpenRouter AI analysis ────────────────────────────────────
def analyze_paper(paper: dict) -> str:
    prompt = f"""Analyze this paper. Be concise and clear.

Title: {paper['title']}
Abstract: {paper['abstract'][:2000]}

Reply in this exact format:
SUMMARY: (2 sentences)
METHODS: (key methods, tools, datasets)
NOVELTY: (what makes it new or important)"""

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek/deepseek-chat:free",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 500
            },
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Analysis failed: {e}"

# ── Telegram handlers ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧬 *LiteriVer Bot*\n\n"
        "Search recent PubMed papers with AI analysis.\n\n"
        "*Commands:*\n"
        "`/search <keywords>` — search last 30 days\n"
        "`/search <keywords> days=7` — custom time window\n"
        "`/search <keywords> max=3` — limit results\n\n"
        "*Example:*\n"
        "`/search spatial transcriptomics cancer days=14 max=3`",
        parse_mode="Markdown"
    )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args)
    if not args:
        await update.message.reply_text("Usage: /search <keywords> [days=30] [max=5]")
        return

    # Parse optional params
    days = 30
    max_results = 5
    keywords = args
    for token in args.split():
        if token.startswith("days="):
            try: days = int(token.split("=")[1]); keywords = keywords.replace(token, "").strip()
            except: pass
        elif token.startswith("max="):
            try: max_results = min(int(token.split("=")[1]), 10); keywords = keywords.replace(token, "").strip()
            except: pass

    msg = await update.message.reply_text(f"🔍 Searching PubMed for *{keywords}*...", parse_mode="Markdown")

    papers = fetch_pubmed(keywords, days, max_results)
    if not papers:
        await msg.edit_text("❌ No papers found. Try different keywords or a wider time window.")
        return

    await msg.edit_text(f"📚 Found {len(papers)} papers. Analyzing with AI (this takes ~{len(papers)*5}s)...")

    for i, paper in enumerate(papers, 1):
        analysis = analyze_paper(paper)
        doi_link = f"https://doi.org/{paper['doi']}" if paper.get('doi') else f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"

        text = (
            f"📄 *Paper {i}/{len(papers)}*\n\n"
            f"*{paper['title']}*\n"
            f"👥 {paper['authors']}\n"
            f"📰 {paper['journal']} · {paper['date']}\n\n"
            f"{analysis}\n\n"
            f"🔗 [Read paper]({doi_link})"
        )
        await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
        await asyncio.sleep(1)

    await update.message.reply_text(f"✅ Done! All {len(papers)} papers analyzed.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ── Main ──────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search))
    print("✅ LiteriVer bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

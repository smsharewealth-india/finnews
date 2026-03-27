"""
FastAPI application entry point.
"""
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import init_db, SessionLocal
from app.config import DEBUG, ADMIN_DEFAULT_USERNAME, ADMIN_DEFAULT_PASSWORD
from app.models.user import User
from app.models.settings import StockSymbol
from app.services.cache import cache_manager
from app.services.scheduler import scheduler_service
from app.services.perplexity import PerplexityService
from app.routers import public, admin
from app.template_filters import register_filters

NIFTY_50_SYMBOLS = [
    ("ADANIENT",   "Adani Enterprises Ltd",              "Conglomerate"),
    ("ADANIPORTS", "Adani Ports & SEZ Ltd",              "Infrastructure"),
    ("APOLLOHOSP", "Apollo Hospitals Enterprise Ltd",    "Healthcare"),
    ("ASIANPAINT", "Asian Paints Ltd",                   "Consumer Goods"),
    ("AXISBANK",   "Axis Bank Ltd",                      "Banking"),
    ("BAJAJ-AUTO", "Bajaj Auto Ltd",                     "Automobile"),
    ("BAJFINANCE", "Bajaj Finance Ltd",                  "Financial Services"),
    ("BAJAJFINSV", "Bajaj Finserv Ltd",                  "Financial Services"),
    ("BPCL",       "Bharat Petroleum Corp Ltd",          "Oil & Gas"),
    ("BHARTIARTL", "Bharti Airtel Ltd",                  "Telecom"),
    ("BRITANNIA",  "Britannia Industries Ltd",           "FMCG"),
    ("CIPLA",      "Cipla Ltd",                          "Pharma"),
    ("COALINDIA",  "Coal India Ltd",                     "Mining"),
    ("DIVISLAB",   "Divi's Laboratories Ltd",            "Pharma"),
    ("DRREDDY",    "Dr. Reddy's Laboratories Ltd",       "Pharma"),
    ("EICHERMOT",  "Eicher Motors Ltd",                  "Automobile"),
    ("GRASIM",     "Grasim Industries Ltd",              "Cement"),
    ("HCLTECH",    "HCL Technologies Ltd",               "IT"),
    ("HDFCBANK",   "HDFC Bank Ltd",                      "Banking"),
    ("HDFCLIFE",   "HDFC Life Insurance Co Ltd",         "Insurance"),
    ("HEROMOTOCO", "Hero MotoCorp Ltd",                  "Automobile"),
    ("HINDALCO",   "Hindalco Industries Ltd",            "Metals"),
    ("HINDUNILVR", "Hindustan Unilever Ltd",             "FMCG"),
    ("ICICIBANK",  "ICICI Bank Ltd",                     "Banking"),
    ("ITC",        "ITC Ltd",                            "FMCG"),
    ("INDUSINDBK", "IndusInd Bank Ltd",                  "Banking"),
    ("INFY",       "Infosys Ltd",                        "IT"),
    ("JSWSTEEL",   "JSW Steel Ltd",                      "Metals"),
    ("KOTAKBANK",  "Kotak Mahindra Bank Ltd",            "Banking"),
    ("LT",         "Larsen & Toubro Ltd",                "Infrastructure"),
    ("M&M",        "Mahindra & Mahindra Ltd",            "Automobile"),
    ("MARUTI",     "Maruti Suzuki India Ltd",            "Automobile"),
    ("NTPC",       "NTPC Ltd",                           "Power"),
    ("NESTLEIND",  "Nestle India Ltd",                   "FMCG"),
    ("ONGC",       "Oil & Natural Gas Corp Ltd",         "Oil & Gas"),
    ("POWERGRID",  "Power Grid Corp of India Ltd",       "Power"),
    ("RELIANCE",   "Reliance Industries Ltd",            "Conglomerate"),
    ("SBILIFE",    "SBI Life Insurance Co Ltd",          "Insurance"),
    ("SBIN",       "State Bank of India",                "Banking"),
    ("SUNPHARMA",  "Sun Pharmaceutical Industries Ltd",  "Pharma"),
    ("TCS",        "Tata Consultancy Services Ltd",      "IT"),
    ("TATACONSUM", "Tata Consumer Products Ltd",         "FMCG"),
    ("TATAMOTORS", "Tata Motors Ltd",                    "Automobile"),
    ("TATASTEEL",  "Tata Steel Ltd",                     "Metals"),
    ("TECHM",      "Tech Mahindra Ltd",                  "IT"),
    ("TITAN",      "Titan Company Ltd",                  "Consumer Goods"),
    ("ULTRACEMCO", "UltraTech Cement Ltd",               "Cement"),
    ("UPL",        "UPL Ltd",                            "Chemicals"),
    ("WIPRO",      "Wipro Ltd",                          "IT"),
]


def init_default_admin(db: Session):
    existing = db.query(User).first()
    if not existing:
        admin_user = User(username=ADMIN_DEFAULT_USERNAME)
        admin_user.set_password(ADMIN_DEFAULT_PASSWORD)
        db.add(admin_user)
        db.commit()
        print(f"Created default admin user: {ADMIN_DEFAULT_USERNAME}")


def seed_nifty50_symbols(db: Session):
    existing_count = db.query(StockSymbol).count()
    if existing_count > 0:
        print(f"Stock symbols already seeded: {existing_count} symbols")
        return
    for symbol, company_name, sector in NIFTY_50_SYMBOLS:
        stock = StockSymbol(
            symbol=symbol, company_name=company_name,
            sector=sector, is_nifty50=True, is_active=True,
        )
        db.add(stock)
    db.commit()
    print(f"Seeded {len(NIFTY_50_SYMBOLS)} Nifty 50 symbols")


def startup_fetch(db: Session):
    perplexity = PerplexityService(db)
    if not perplexity.is_configured():
        print("Perplexity API key not configured. Skipping startup fetch.")
        return
    stats = cache_manager.get_cache_stats()
    if stats["total_news"] == 0:
        print("Cache is empty. Fetching initial news...")
        from app.services.news_fetcher import NewsFetcher
        fetcher = NewsFetcher(db)
        results = fetcher.fetch_all_jobs(triggered_by="startup")
        print(f"Startup fetch complete: {results}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting FinSights...")
    init_db()
    print("Database initialized")
    db = SessionLocal()
    try:
        init_default_admin(db)
        seed_nifty50_symbols(db)
        cache_manager.load_from_db(db)
        cache_manager.load_symbols(db)
        print(f"Cache loaded: {cache_manager.get_cache_stats()}")
        scheduler_service.init_jobs_from_db(db)
        scheduler_service.start()
        print("Scheduler started")
        # startup_fetch(db)  # Uncomment to auto-fetch on startup
    finally:
        db.close()
    yield
    print("Shutting down FinSights...")
    scheduler_service.stop()
    print("Scheduler stopped")


# ── App ───────────────────────────────────────────────────────
app = FastAPI(
    title="FinSights",
    description="Indian Market News Summary Platform",
    version="1.0.0",
    lifespan=lifespan,
    debug=DEBUG,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(public.router)
app.include_router(admin.router)
register_filters(public.templates)
register_filters(admin.templates)


# ── Health ────────────────────────────────────────────────────
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "scheduler_running": scheduler_service.is_running(),
        "cache_stats": cache_manager.get_cache_stats(),
    }


# ─────────────────────────────────────────────────────────────
# JSON API  —  for n8n / external automation
# Reads from in-memory cache only (no DB hit, always fast)
# ─────────────────────────────────────────────────────────────

def _fmt(n: dict) -> dict:
    """Serialize a news dict to clean API-friendly format."""
    return {
        "id":          n.get("id"),
        "title":       n.get("title", ""),
        "summary":     n.get("summary") or n.get("content", ""),
        "category":    n.get("category", ""),
        "subcategory": n.get("subcategory", ""),
        "source_name": n.get("source_name", ""),
        "source_url":  n.get("source_url", ""),
        "published_at":n.get("published_at", ""),
        "fetched_at":  n.get("fetched_at", ""),
        "is_featured": n.get("is_featured", False),
        "sentiment":   n.get("sentiment_score"),
    }


@app.get("/api/news")
async def api_news(
    category: Optional[str] = Query(None, description="market | sector | macro | regulation"),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Latest news as JSON.
    GET /api/news                          → all categories, newest 20
    GET /api/news?category=market&limit=15 → market only
    """
    try:
        if category:
            items = cache_manager.get_news_by_category(category.lower(), limit=limit)
        else:
            items = cache_manager.get_latest_news(limit=limit)
        return JSONResponse(content={
            "status":   "ok",
            "count":    len(items),
            "category": category or "all",
            "news":     [_fmt(n) for n in items],
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/news/summary")
async def api_news_summary():
    """
    Quick snapshot — counts per category + latest headline.
    Use in n8n to check if fresh news exists before fetching full data.
    """
    try:
        stats  = cache_manager.get_cache_stats()
        latest = cache_manager.get_latest_news(limit=1)
        return JSONResponse(content={
            "status":       "ok",
            "total":        stats["total_news"],
            "by_category":  stats["categories"],
            "latest_title": latest[0].get("title", "") if latest else "",
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/news/market")
async def api_market_news(limit: int = Query(15, ge=1, le=50)):
    """Market news — shortcut for /api/news?category=market"""
    try:
        items = cache_manager.get_news_by_category("market", limit=limit)
        return JSONResponse(content={"status": "ok", "count": len(items), "news": [_fmt(n) for n in items]})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/news/sector")
async def api_sector_news(limit: int = Query(15, ge=1, le=50)):
    """Sector news — shortcut for /api/news?category=sector"""
    try:
        items = cache_manager.get_news_by_category("sector", limit=limit)
        return JSONResponse(content={"status": "ok", "count": len(items), "news": [_fmt(n) for n in items]})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/news/featured")
async def api_featured_news(limit: int = Query(10, ge=1, le=30)):
    """Featured / top news only."""
    try:
        items = cache_manager.get_featured_news(limit=limit)
        return JSONResponse(content={"status": "ok", "count": len(items), "news": [_fmt(n) for n in items]})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/news/search")
async def api_search_news(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(20, ge=1, le=50),
):
    """Full-text search across all cached news titles and summaries."""
    try:
        results = cache_manager.search_news(q, limit=limit)
        return JSONResponse(content={
            "status": "ok", "query": q,
            "count":  len(results),
            "news":   [_fmt(n) for n in results],
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
